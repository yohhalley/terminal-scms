from django.db import models
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db.models import Q
from django.db.models.signals import post_save
from django.core.exceptions import ValidationError
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import math
import uuid


def validate_iana_timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ValidationError('Enter a valid IANA timezone such as Europe/Madrid.')


def calculated_operation_minutes(total_moves, equipment_split, productivity_per_unit):
    if not total_moves or not equipment_split or not productivity_per_unit:
        return None
    hourly_productivity = Decimal(equipment_split) * Decimal(productivity_per_unit)
    if hourly_productivity <= 0:
        return None
    return int(
        (Decimal(total_moves) * Decimal(60) / hourly_productivity).quantize(
            Decimal('1'), rounding=ROUND_CEILING
        )
    )


def format_duration_minutes(minutes):
    if minutes is None:
        return None
    hours, remaining_minutes = divmod(int(minutes), 60)
    return f'{hours} hours and {remaining_minutes} minutes'


class IntegerRangeField(models.IntegerField):
    def __init__(self, verbose_name=None, name=None, min_value=None, max_value=None, **kwargs):
        self.min_value, self.max_value = min_value, max_value
        models.IntegerField.__init__(self, verbose_name, name, **kwargs)

    def formfield(self, **kwargs):
        defaults = {'min_value': self.min_value, 'max_value': self.max_value}
        defaults.update(kwargs)
        return super(IntegerRangeField, self).formfield(**defaults)


###################################
class ISOType(models.Model):
    L20 = '20'
    L40 = '40'
    L45 = '45'

    HNC = 'NC'
    HHC = 'HC'

    LENGTHS = [
        (L20, '20\''),
        (L40, '40\''),
        (L45, '45\'')
    ]

    HEIGHTS = [
        (HNC, 'regular height (9\')'),
        (HHC, 'high cube (9\' 6\")')
    ]
    iso_type = models.CharField(
        validators=[RegexValidator(regex='^.{4}$', message='Length has to be 4', code='nomatch')], max_length=4,
        unique=True)
    length = models.CharField(
        max_length=3,
        choices=LENGTHS,
        default=L20,
    )

    height = models.CharField(
        max_length=3,
        choices=HEIGHTS,
        default=HNC,
    )

    is_reefer = models.BooleanField(default=0)
    is_oog = models.BooleanField(default=0)

    def __str__(self):
        return self.iso_type


###################################
class ContainerOwner(models.Model):
    name = models.CharField(max_length=30, unique=True)
    code = models.CharField(validators=[RegexValidator(regex='^.{4}$', message='Length has to be 4', code='nomatch')],
                            max_length=4, unique=True)
    is_shippingline = models.BooleanField(default=0)

    def __str__(self):
        return self.name


###################################
class ContainerUnit(models.Model):
    iso_type = models.ForeignKey(
        'ISOType',
        on_delete=models.RESTRICT, null=True
    )
    owner = models.ForeignKey(
        'ContainerOwner',
        on_delete=models.RESTRICT,
    )

    container_number = models.CharField(
        validators=[RegexValidator(regex='^.{11}$', message='Length has to be 11', code='nomatch')], max_length=11,
        primary_key=True)
    is_active = models.BooleanField(default=1)
    is_in_terminal = models.BooleanField(default=0)

    def __str__(self):
        return self.container_number


###################################
class ContainerVisit(models.Model):
    IMPORT = 'I'
    EXPORT = 'E'
    TRANSSHIPMENT = 'T'
    STORAGE = 'S'
    THROUGH = 'O'
    TERMINAL = 'M'

    CATEGORIES = [
        (IMPORT, 'Import'),
        (EXPORT, 'Export'),
        (TRANSSHIPMENT, 'Transshipment'),
        (STORAGE, 'Storage'),
        (THROUGH, 'Through'),
        (TERMINAL, 'Owned by Terminal')
    ]

    category = models.CharField(
        max_length=1,
        choices=CATEGORIES,
        default=STORAGE,
    )

    container_number = models.ForeignKey(
        'ContainerUnit',
        on_delete=models.RESTRICT, blank=True, null=True
    )
    ib_carrier = models.ForeignKey(
        'CarrierVisit',
        on_delete=models.RESTRICT,
        related_name='ib_carrier', blank=True, null=True
    )
    ob_carrier = models.ForeignKey(
        'CarrierVisit',
        on_delete=models.RESTRICT,
        related_name='ob_carrier', blank=True, null=True
    )

    location = models.ForeignKey(
        'Location',
        on_delete=models.RESTRICT,
    )
    eta = models.DateField(blank=True, null=True)
    etd = models.DateField(blank=True, null=True)
    weight = models.IntegerField(default=0)
    pod = models.ForeignKey(
        'POD',
        on_delete=models.RESTRICT,
        related_name='pod', blank=True, null=True,
    )
    pod2 = models.ForeignKey(
        'POD',
        on_delete=models.RESTRICT,
        related_name='pod2', blank=True, null=True,
    )
    requires_power = models.BooleanField(default=0)
    is_imdg = models.BooleanField(default=0)
    latitude = models.FloatField(blank=True, null=True)
    longitude = models.FloatField(blank=True, null=True)
    z_position = models.FloatField(blank=True, null=True)
    bill_of_lading = models.CharField(max_length=20, blank=True, null=True)
    booking = models.CharField(max_length=20, blank=True, null=True)

    # New fields for status tracking
    PREADVISED = 'Preadvised'
    ARRIVED = 'Arrived'
    AT_YARD = 'AtYard'
    AT_QC = 'AtQC'
    ON_EQUIPMENT = 'OnEquipment'
    ON_VESSEL = 'OnVessel'
    DEPARTED = 'Departed'
    COMPLETED = 'Completed'

    STATUS_CHOICES = [
        (PREADVISED, 'Preadvised'),
        (ARRIVED, 'Arrived'),
        (AT_YARD, 'At Yard'),
        (AT_QC, 'At QC'),
        (ON_EQUIPMENT, 'On Equipment'),
        (ON_VESSEL, 'On Vessel'),
        (DEPARTED, 'Departed'),
        (COMPLETED, 'Completed'),
    ]

    status = models.CharField(
        max_length=15,
        choices=STATUS_CHOICES,
        default=PREADVISED,
    )
    current_che = models.ForeignKey(
        'Che', related_name='carried_container_visits',
        on_delete=models.RESTRICT, blank=True, null=True,
        help_text='CHE temporarily carrying this container during an active move.',
    )
    last_updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        if self.container_number_id:
            return self.container_number.container_number
        return f"ContainerVisit #{self.pk}"


###################################
class Location(models.Model):
    NA = 'NA'
    TL = 'TL'
    ID = 'ID'
    VL = 'VL'
    TK = 'TK'
    TN = 'TN'

    LOCATION_TYPES = [
        (NA, 'Unknown'),
        (TL, 'Terminal'),
        (ID, 'Inland Depot'),
        (VL, 'Vessel'),
        (TK, 'Truck'),
        (TN, 'Train'),
    ]

    name = models.CharField(max_length=30, unique=True)
    location_type = models.CharField(
        max_length=2,
        choices=LOCATION_TYPES,
        default=NA,
    )
    latitude = models.FloatField(blank=True, null=True, help_text='GPS latitude coordinate')
    longitude = models.FloatField(blank=True, null=True, help_text='GPS longitude coordinate')
    map_origin_latitude = models.FloatField(blank=True, null=True, help_text='Latitude of the local map origin')
    map_origin_longitude = models.FloatField(blank=True, null=True, help_text='Longitude of the local map origin')
    map_width_m = models.FloatField(default=1000.0, help_text='Local map width in meters')
    map_height_m = models.FloatField(default=1000.0, help_text='Local map height in meters')
    map_units_per_meter = models.FloatField(default=1.0, help_text='Godot/map units represented by one real-world meter')
    default_zoom = models.FloatField(
        blank=True,
        null=True,
        validators=[RegexValidator(regex=r'^[0-9]*\.?[0-9]+$', message='Must be a positive number')],
        help_text='Default zoom level for map view'
    )
    angle = models.FloatField(blank=True, null=True, help_text='Rotation angle in degrees (0-360)')
    timezone = models.CharField(
        max_length=64,
        default='UTC',
        validators=[validate_iana_timezone],
        help_text='IANA timezone used for local terminal operations',
    )

    def __str__(self):
        return "%s" % (self.name)


###################################
class LayoutVersion(models.Model):
    DRAFT = 'draft'
    PUBLISHED = 'published'
    ARCHIVED = 'archived'
    STATUS_CHOICES = [
        (DRAFT, 'Draft'),
        (PUBLISHED, 'Published'),
        (ARCHIVED, 'Archived'),
    ]

    location = models.ForeignKey(
        'Location', related_name='layout_versions', on_delete=models.CASCADE,
    )
    version = models.PositiveIntegerField(default=1)
    name = models.CharField(max_length=100, default='Working layout')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=DRAFT)
    is_current = models.BooleanField(default=True)
    based_on = models.ForeignKey(
        'self', related_name='derived_versions', on_delete=models.SET_NULL,
        blank=True, null=True,
    )
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        'auth.User', related_name='created_layout_versions', on_delete=models.SET_NULL,
        blank=True, null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['location_id', '-version']
        constraints = [
            models.UniqueConstraint(
                fields=['location', 'version'], name='unique_layout_version_per_location',
            ),
            models.UniqueConstraint(
                fields=['location'], condition=Q(is_current=True),
                name='one_current_layout_per_location',
            ),
        ]

    def __str__(self):
        return f'{self.location.name} layout v{self.version}'


class LayoutAsset(models.Model):
    TERMINAL = 'terminal'
    LAND = 'land'
    WATER = 'water'
    QUAY = 'quay'
    YARD = 'yard'
    YARD_BLOCK = 'yard_block'
    TRANSFER_ZONE = 'transfer_zone'
    PARKING = 'parking'
    BUILDING = 'building'
    ROAD = 'road'
    RAIL_AREA = 'rail_area'
    GATE_AREA = 'gate_area'
    WORKSHOP = 'workshop'
    OTHER = 'other'
    ASSET_KIND_CHOICES = [
        (TERMINAL, 'Terminal boundary'),
        (LAND, 'Land'),
        (WATER, 'Water'),
        (QUAY, 'Quay'),
        (YARD, 'Yard'),
        (YARD_BLOCK, 'Yard block'),
        (TRANSFER_ZONE, 'Transfer zone'),
        (PARKING, 'Parking'),
        (BUILDING, 'Building'),
        (ROAD, 'Road corridor'),
        (RAIL_AREA, 'Rail area'),
        (GATE_AREA, 'Gate area'),
        (WORKSHOP, 'Workshop'),
        (OTHER, 'Other'),
    ]
    CHE_PLACEMENT_KINDS = (QUAY, YARD_BLOCK, PARKING, RAIL_AREA)

    layout_version = models.ForeignKey(
        LayoutVersion, related_name='assets', on_delete=models.CASCADE,
    )
    parent = models.ForeignKey(
        'self', related_name='children', on_delete=models.CASCADE,
        blank=True, null=True,
    )
    access_policy = models.ForeignKey(
        'AccessPolicy', related_name='assets', on_delete=models.SET_NULL,
        blank=True, null=True,
    )
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=100)
    asset_kind = models.CharField(max_length=24, choices=ASSET_KIND_CHOICES)
    geometry = models.JSONField(
        default=dict,
        help_text='GeoJSON geometry using WGS-84 longitude/latitude coordinates.',
    )
    heading_deg = models.FloatField(default=0.0, help_text='Heading relative to true north')
    elevation_m = models.FloatField(default=0.0)
    is_visible = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['asset_kind', 'code']
        constraints = [
            models.UniqueConstraint(
                fields=['layout_version', 'code'], name='unique_layout_asset_code',
            )
        ]

    @property
    def location(self):
        return self.layout_version.location

    def clean(self):
        if self.parent_id and self.parent.layout_version_id != self.layout_version_id:
            raise ValidationError({'parent': 'Parent assets must belong to the same layout version.'})
        if self.access_policy_id and self.access_policy.layout_version_id != self.layout_version_id:
            raise ValidationError({'access_policy': 'The policy must belong to the same layout version.'})

    def __str__(self):
        return f'{self.code} - {self.name}'


class AccessPolicy(models.Model):
    layout_version = models.ForeignKey(
        LayoutVersion, related_name='access_policies', on_delete=models.CASCADE,
    )
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=100)
    allowed_che_types = models.ManyToManyField(
        'EquipmentType', related_name='layout_access_policies', blank=True,
    )
    allow_loaded = models.BooleanField(default=True)
    allow_empty = models.BooleanField(default=True)
    max_width_m = models.FloatField(blank=True, null=True)
    max_height_m = models.FloatField(blank=True, null=True)
    max_weight_t = models.FloatField(blank=True, null=True)
    rules = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['code']
        constraints = [
            models.UniqueConstraint(
                fields=['layout_version', 'code'], name='unique_access_policy_code',
            )
        ]

    def __str__(self):
        return self.name


class LinearAxis(models.Model):
    ROAD_CENTERLINE = 'road_centerline'
    RAIL_TRACK = 'rail_track'
    CRANE_RAIL = 'crane_rail'
    QUAY_FACE = 'quay_face'
    TRANSFER_LANE = 'transfer_lane'
    FREE_DRIVE_GUIDE = 'free_drive_guide'
    AXIS_KIND_CHOICES = [
        (ROAD_CENTERLINE, 'Road centerline'),
        (RAIL_TRACK, 'Rail track'),
        (CRANE_RAIL, 'Crane rail'),
        (QUAY_FACE, 'Quay face'),
        (TRANSFER_LANE, 'Transfer lane'),
        (FREE_DRIVE_GUIDE, 'Free-drive guide'),
    ]
    FORWARD = 'forward'
    REVERSE = 'reverse'
    BOTH = 'both'
    DIRECTION_CHOICES = [
        (FORWARD, 'Start to end'),
        (REVERSE, 'End to start'),
        (BOTH, 'Both directions'),
    ]

    asset = models.ForeignKey(LayoutAsset, related_name='linear_axes', on_delete=models.CASCADE)
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=100)
    axis_kind = models.CharField(max_length=24, choices=AXIS_KIND_CHOICES)
    points = models.JSONField(
        default=list,
        help_text='Ordered WGS-84 centerline points as [[longitude, latitude], ...].',
    )
    direction = models.CharField(max_length=7, choices=DIRECTION_CHOICES, default=BOTH)
    lane_count = models.PositiveSmallIntegerField(default=1)
    capacity = models.PositiveIntegerField(default=1)
    width_m = models.FloatField(blank=True, null=True)
    speed_limit_kph = models.FloatField(blank=True, null=True)
    access_policy = models.ForeignKey(
        AccessPolicy, related_name='axes', on_delete=models.SET_NULL,
        blank=True, null=True,
    )
    is_enabled = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['code']
        constraints = [
            models.UniqueConstraint(
                fields=['asset', 'code'], name='unique_linear_axis_code_per_asset',
            )
        ]

    @property
    def layout_version(self):
        return self.asset.layout_version

    def clean(self):
        if self.access_policy_id and self.access_policy.layout_version_id != self.asset.layout_version_id:
            raise ValidationError({'access_policy': 'The policy must belong to the axis layout version.'})

    def __str__(self):
        return f'{self.code} - {self.name}'


class LinearMarker(models.Model):
    BOLLARD = 'bollard'
    STOP = 'stop'
    REFERENCE = 'reference'
    LIMIT = 'limit'
    MARKER_KIND_CHOICES = [
        (BOLLARD, 'Bollard'),
        (STOP, 'Stop'),
        (REFERENCE, 'Reference'),
        (LIMIT, 'Travel limit'),
    ]

    axis = models.ForeignKey(LinearAxis, related_name='markers', on_delete=models.CASCADE)
    marker_kind = models.CharField(max_length=16, choices=MARKER_KIND_CHOICES)
    sequence = models.PositiveIntegerField()
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=100)
    chainage_m = models.FloatField(help_text='Distance from the axis start in meters')
    lateral_offset_m = models.FloatField(default=0.0)
    latitude = models.FloatField()
    longitude = models.FloatField()
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['axis_id', 'sequence']
        constraints = [
            models.UniqueConstraint(
                fields=['axis', 'sequence'], name='unique_marker_sequence_per_axis',
            ),
            models.UniqueConstraint(
                fields=['axis', 'code'], name='unique_marker_code_per_axis',
            ),
        ]

    def __str__(self):
        return f'{self.axis.code} / {self.name}'


class BerthSegment(models.Model):
    WATER_LEFT = 'left'
    WATER_RIGHT = 'right'
    WATER_SIDE_CHOICES = [(WATER_LEFT, 'Left'), (WATER_RIGHT, 'Right')]
    BOLLARDS_FORWARD = 'forward'
    BOLLARDS_REVERSE = 'reverse'
    BOLLARD_DIRECTION_CHOICES = [
        (BOLLARDS_FORWARD, 'Start to end'),
        (BOLLARDS_REVERSE, 'End to start'),
    ]

    quay_asset = models.ForeignKey(
        LayoutAsset, related_name='berth_segments', on_delete=models.CASCADE,
    )
    quay_face_axis = models.ForeignKey(
        LinearAxis, related_name='berth_segments', on_delete=models.CASCADE,
    )
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=100)
    start_chainage_m = models.FloatField(default=0.0)
    end_chainage_m = models.FloatField()
    water_side = models.CharField(max_length=5, choices=WATER_SIDE_CHOICES, default=WATER_LEFT)
    bollard_direction = models.CharField(
        max_length=7, choices=BOLLARD_DIRECTION_CHOICES, default=BOLLARDS_FORWARD,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['quay_asset_id', 'start_chainage_m']
        constraints = [
            models.UniqueConstraint(
                fields=['quay_asset', 'code'], name='unique_berth_segment_code_per_quay',
            )
        ]

    @property
    def location(self):
        return self.quay_asset.layout_version.location

    @property
    def bollards(self):
        low = min(self.start_chainage_m, self.end_chainage_m)
        high = max(self.start_chainage_m, self.end_chainage_m)
        return self.quay_face_axis.markers.filter(
            marker_kind=LinearMarker.BOLLARD,
            chainage_m__gte=low,
            chainage_m__lte=high,
        ).order_by('sequence')

    @property
    def length_m(self):
        return abs(self.end_chainage_m - self.start_chainage_m)

    def clean(self):
        if self.quay_asset.asset_kind != LayoutAsset.QUAY:
            raise ValidationError({'quay_asset': 'A berth segment must belong to a quay asset.'})
        if self.quay_face_axis.asset_id != self.quay_asset_id:
            raise ValidationError({'quay_face_axis': 'The quay face axis must belong to the selected quay.'})
        if self.quay_face_axis.axis_kind != LinearAxis.QUAY_FACE:
            raise ValidationError({'quay_face_axis': 'Select an axis with quay-face kind.'})
        if self.start_chainage_m == self.end_chainage_m:
            raise ValidationError({'end_chainage_m': 'A berth segment must have a positive length.'})

    def __str__(self):
        return f'{self.quay_asset.code} / {self.name}'


###################################
class StorageGrid(models.Model):
    AUTOMATED = 'automated'
    MANUAL = 'manual'
    CHASSIS = 'chassis'
    HEAP = 'heap'
    GRID_KIND_CHOICES = [
        (AUTOMATED, 'Automated stacking block'),
        (MANUAL, 'Manual stacking block'),
        (CHASSIS, 'Chassis yard'),
        (HEAP, 'Heap zone'),
    ]

    layout_asset = models.OneToOneField(
        LayoutAsset, related_name='storage_grid', on_delete=models.CASCADE,
    )
    grid_kind = models.CharField(max_length=16, choices=GRID_KIND_CHOICES, default=AUTOMATED)

    name = models.CharField(max_length=30, unique=False)
    max_bays = IntegerRangeField(min_value=1, max_value=100)
    max_rows = IntegerRangeField(min_value=1, max_value=100)
    max_tier = IntegerRangeField(min_value=1, max_value=20)

    # Map anchor selected by the editor. At heading 0 the grid grows north and east.
    latitude = models.FloatField(blank=True, null=True, help_text='Latitude of the block drawing anchor')
    longitude = models.FloatField(blank=True, null=True, help_text='Longitude of the block drawing anchor')
    heading_deg = models.FloatField(blank=True, null=True, help_text='Block rotation/heading in degrees')
    slot_heading_deg = models.FloatField(default=0.0, help_text='Slot rotation relative to the block heading')

    # Default pile dimensions (meters) - 20' ISO container approx values
    pile_length_m = models.FloatField(default=6.058, help_text="Pile length in meters (default 20')")
    pile_width_m = models.FloatField(default=2.438, help_text='Pile width in meters (default 20")')
    pile_height_m = models.FloatField(default=2.591, help_text='Pile height in meters (default 20")')

    # Grid origin and ordering
    initial_bay = models.CharField(max_length=10, default='1')
    initial_row = models.CharField(max_length=10, default='1')

    FORWARD = 'forward'
    REVERSE = 'reverse'
    ORDER_CHOICES = [
        (FORWARD, 'forward'),
        (REVERSE, 'reverse'),
    ]

    bay_order = models.CharField(max_length=7, choices=ORDER_CHOICES, default=FORWARD)
    row_order = models.CharField(max_length=7, choices=ORDER_CHOICES, default=FORWARD)

    NUMBERS = '1,2,3...'
    ODD_NUMBERS = '1,3,5...'
    LETTERS = 'A,B,C...'
    NAMING_CHOICES = [
        (NUMBERS, '1,2,3...'),
        (ODD_NUMBERS, '1,3,5...'),
        (LETTERS, 'A,B,C...'),
    ]

    bay_naming = models.CharField(max_length=8, choices=NAMING_CHOICES, default=NUMBERS)
    row_naming = models.CharField(max_length=8, choices=NAMING_CHOICES, default=NUMBERS)

    # Whether bay numbering follows the block heading axis
    bay_use_heading_axis = models.BooleanField(default=False)

    # Padding around the block (meters)
    padding_top_m = models.FloatField(default=0.0, blank=True, null=True, help_text='Top padding (m) around block')
    padding_bottom_m = models.FloatField(default=0.0, blank=True, null=True, help_text='Bottom padding (m) around block')
    padding_left_m = models.FloatField(default=0.0, blank=True, null=True, help_text='Left padding (m) around block')
    padding_right_m = models.FloatField(default=0.0, blank=True, null=True, help_text='Right padding (m) around block')

    # Spacing between bays and rows (meters)
    bay_spacing_m = models.FloatField(default=0.4, help_text='Spacing between bays in meters')
    row_spacing_m = models.FloatField(default=0.4, help_text='Spacing between rows in meters')

    is_heapzone = models.BooleanField(default=0)

    objects = models.Manager() # The default manager.

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['layout_asset', 'name'], name='unique_grid_name_per_asset',
            )
        ]

    @property
    def location(self):
        return self.layout_asset.layout_version.location

    def save(self, *args, **kwargs):
        if self.latitude is None:
            self.latitude = self.location.map_origin_latitude or self.location.latitude
        if self.longitude is None:
            self.longitude = self.location.map_origin_longitude or self.location.longitude
        if self.latitude is None or self.longitude is None:
            raise ValidationError('A block requires latitude and longitude coordinates.')
        super().save(*args, **kwargs)

    def __str__(self):
        return "%s-%s" % (self.location, self.name)

###################################
class Pile(models.Model):
    storage_grid = models.ForeignKey(
        'StorageGrid', related_name='piles',
        on_delete=models.CASCADE,
    )
    bay = models.IntegerField(default=0)
    row = models.IntegerField(default=0)
    tiers = models.IntegerField(default=0)
    allows_in = models.BooleanField(default=True)
    allows_out = models.BooleanField(default=True)
    is_restricted = models.BooleanField(default=False)
    allows_20 = models.BooleanField(default=False)
    allows_40 = models.BooleanField(default=False)
    allows_45 = models.BooleanField(default=False)

    latitude = models.FloatField(blank=True, null=True)
    longitude = models.FloatField(blank=True, null=True)

    # Per-pile margin adjustments (meters) to handle special cases
    margin_top_m = models.FloatField(default=0.0, blank=True, null=True, help_text='Top margin for this pile (m)')
    margin_bottom_m = models.FloatField(default=0.0, blank=True, null=True, help_text='Bottom margin for this pile (m)')
    margin_left_m = models.FloatField(default=0.0, blank=True, null=True, help_text='Left margin for this pile (m)')
    margin_right_m = models.FloatField(default=0.0, blank=True, null=True, help_text='Right margin for this pile (m)')

    class Meta:
        unique_together = ('storage_grid', 'bay', 'row')

    def __str__(self):
        return "%s-%s-%s-%s" % (self.storage_grid.location.name, self.storage_grid.name, self.bay, self.row)

    def get_bay_name(self):
        """Generate bay name/identifier based on block naming convention."""
        return self._grid_label('bay')

    def get_row_name(self):
        """Generate row name/identifier based on block naming convention."""
        return self._grid_label('row')

    def _grid_label(self, axis):
        if not self.storage_grid:
            return str(getattr(self, axis))
        physical = getattr(self, axis)
        count = getattr(self.storage_grid, f'max_{axis}s')
        order = getattr(self.storage_grid, f'{axis}_order')
        logical = count - physical + 1 if order == StorageGrid.REVERSE else physical
        naming = getattr(self.storage_grid, f'{axis}_naming')
        initial = str(getattr(self.storage_grid, f'initial_{axis}') or '1').strip()
        if naming == StorageGrid.LETTERS:
            number = 0
            for char in initial.upper():
                if not ('A' <= char <= 'Z'):
                    number = 1
                    break
                number = number * 26 + ord(char) - 64
            number = max(1, number) + logical - 1
            result = ''
            while number:
                number -= 1
                result = chr(65 + number % 26) + result
                number //= 26
            return result
        try:
            start = int(initial)
        except ValueError:
            start = 1
        step = 2 if naming == StorageGrid.ODD_NUMBERS else 1
        return str(start + (logical - 1) * step)

    def get_pile_name(self):
        """Generate full pile identifier (e.g., 'A-01' or '1-2')."""
        return f"{self.get_bay_name()}-{self.get_row_name()}"

    def save(self, *args, **kwargs):
        # Set container size restrictions based on bay number
        if self.bay % 2 == 1:  # Odd numbered bays
            self.allows_20 = True
            self.allows_40 = False
            self.allows_45 = False
        else:  # Even numbered bays
            self.allows_20 = False
            self.allows_40 = True
            self.allows_45 = True

        super().save(*args, **kwargs)

    def can_accept_container(self, container_visit):
        """
        Check if this pile can accept a container based on its size.
        Returns (bool, str) tuple: (can_accept, reason)
        """
        if not container_visit or not container_visit.container_number:
            return False, "No container specified"

        container = container_visit.container_number
        iso_type = container.iso_type

        if iso_type.length == '20' and not self.allows_20:
            return False, "20' containers are not allowed in this bay"
        elif iso_type.length == '40' and not self.allows_40:
            return False, "40' containers are not allowed in this bay"
        elif iso_type.length == '45' and not self.allows_45:
            return False, "45' containers are not allowed in this bay"

        return True, "Container size is compatible with this bay"


###################################
class Position(models.Model):
    container_visit = models.OneToOneField(
        'ContainerVisit',
        related_name='position',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    pile = models.ForeignKey(
        'Pile',
        related_name='positions',
        on_delete=models.CASCADE
    )
    is_reserved = models.BooleanField(default=False)
    is_occupied = models.BooleanField(default=False)
    is_powered = models.BooleanField(default=False)
    tier = IntegerRangeField(min_value=1, max_value=20)

    class Meta:
        unique_together = ('pile', 'tier')

    def __str__(self):
        return "%s-%s-%s-%s" % (self.pile.storage_grid, self.pile.bay, self.pile.row, self.tier)

    def validate_physical_rules(self):
        """Validate that the position follows physical rules."""
        if self.container_visit:
            # Check if there are any gaps below this position
            lower_positions = self.pile.positions.filter(tier__lt=self.tier).order_by('tier')
            for pos in lower_positions:
                if not pos.is_occupied:
                    raise ValidationError(
                        f"Cannot place container in tier {self.tier} when tier {pos.tier} is empty"
                    )
        else:
            # Check if removing this container would leave containers floating
            higher_positions = self.pile.positions.filter(tier__gt=self.tier).order_by('tier')
            if higher_positions.filter(is_occupied=True).exists():
                raise ValidationError(
                    f"Cannot remove container from tier {self.tier} when there are containers above it"
                )

    def save(self, *args, **kwargs):
        # Validate that position is not already occupied
        if self.container_visit and not self._state.adding:
            # Check if another position already has this container
            existing_position = Position.objects.filter(
                container_visit=self.container_visit
            ).exclude(pk=self.pk).first()
            if existing_position:
                raise ValidationError(
                    f"Container {self.container_visit} is already assigned to position {existing_position}"
                )

            # Validate container size restrictions
            can_accept, reason = self.pile.can_accept_container(self.container_visit)
            if not can_accept:
                raise ValidationError(reason)

            # Validate allows_in when assigning a container
            if not self.pile.allows_in:
                raise ValidationError("This pile does not allow containers to be placed in it")

            # Validate physical rules when placing a container
            self.validate_physical_rules()

        # Validate allows_out when removing a container
        if self._state.adding is False and self.container_visit is None:
            # Check if we're removing a container
            old_position = Position.objects.get(pk=self.pk)
            if old_position.container_visit is not None and not self.pile.allows_out:
                raise ValidationError("This pile does not allow containers to be removed from it")

            # Validate physical rules when removing a container
            self.validate_physical_rules()

        # Update is_occupied based on container_visit
        self.is_occupied = bool(self.container_visit)

        super().save(*args, **kwargs)


###################################
class TransferInterface(models.Model):
    DIRECT_HANDOFF = 'direct_handoff'
    VEHICLE_LANE = 'vehicle_lane'
    GROUND_BUFFER = 'ground_buffer'
    PLATFORM = 'platform'
    CHASSIS = 'chassis'
    STACK_ATTACK = 'stack_attack'
    INTERFACE_KIND_CHOICES = [
        (DIRECT_HANDOFF, 'Direct handoff'),
        (VEHICLE_LANE, 'Vehicle lane'),
        (GROUND_BUFFER, 'Ground buffer'),
        (PLATFORM, 'Platform'),
        (CHASSIS, 'Chassis position'),
        (STACK_ATTACK, 'Stack attack zone'),
    ]
    LEFT = 'left'
    RIGHT = 'right'
    START = 'start'
    END = 'end'
    INTERNAL = 'internal'
    SIDE_CHOICES = [
        (LEFT, 'Left'), (RIGHT, 'Right'), (START, 'Start'),
        (END, 'End'), (INTERNAL, 'Internal'),
    ]

    asset = models.ForeignKey(
        LayoutAsset, related_name='transfer_interfaces', on_delete=models.CASCADE,
    )
    storage_grid = models.ForeignKey(
        StorageGrid, related_name='transfer_interfaces', on_delete=models.CASCADE,
        blank=True, null=True,
    )
    axis = models.ForeignKey(
        LinearAxis, related_name='transfer_interfaces', on_delete=models.SET_NULL,
        blank=True, null=True,
    )
    road_axis = models.ForeignKey(
        LinearAxis, related_name='road_interfaces', on_delete=models.SET_NULL,
        blank=True, null=True,
        help_text='Adjacent road centerline used to enter or leave this interface.',
    )
    access_policy = models.ForeignKey(
        AccessPolicy, related_name='transfer_interfaces', on_delete=models.SET_NULL,
        blank=True, null=True,
    )
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=100)
    interface_kind = models.CharField(max_length=20, choices=INTERFACE_KIND_CHOICES)
    side = models.CharField(max_length=10, choices=SIDE_CHOICES, default=INTERNAL)
    geometry = models.JSONField(default=dict, blank=True)
    bay_start = models.PositiveIntegerField(blank=True, null=True)
    bay_end = models.PositiveIntegerField(blank=True, null=True)
    row_start = models.PositiveIntegerField(blank=True, null=True)
    row_end = models.PositiveIntegerField(blank=True, null=True)
    capacity = models.PositiveIntegerField(default=1)
    lane_count = models.PositiveSmallIntegerField(default=1)
    lane_spacing_m = models.FloatField(default=3.5)
    position_reference = models.CharField(
        max_length=10, choices=[('fixed', 'Fixed'), ('che', 'Equipment relative')], default='fixed',
    )
    reference_che = models.ForeignKey(
        'Che', related_name='referenced_interfaces', on_delete=models.RESTRICT, blank=True, null=True,
    )
    longitudinal_offset_m = models.FloatField(default=0.0)
    lateral_offset_m = models.FloatField(default=0.0)
    position_spacing_m = models.FloatField(default=6.5)
    cargo_may_rest = models.BooleanField(default=False)
    requires_vehicle_present = models.BooleanField(default=False)
    is_enabled = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['asset_id', 'code']
        constraints = [
            models.UniqueConstraint(
                fields=['asset', 'code'], name='unique_transfer_interface_code_per_asset',
            )
        ]

    @property
    def layout_version(self):
        return self.asset.layout_version

    def clean(self):
        errors = {}
        if self.storage_grid_id and self.storage_grid.layout_asset.layout_version_id != self.asset.layout_version_id:
            errors['storage_grid'] = 'The storage grid must belong to the same layout version.'
        if self.axis_id and self.axis.asset.layout_version_id != self.asset.layout_version_id:
            errors['axis'] = 'The axis must belong to the same layout version.'
        if self.road_axis_id:
            if self.road_axis.asset.layout_version_id != self.asset.layout_version_id:
                errors['road_axis'] = 'The road must belong to the same layout version.'
            elif self.road_axis.axis_kind != LinearAxis.ROAD_CENTERLINE:
                errors['road_axis'] = 'Select a road-centerline axis.'
        if self.access_policy_id and self.access_policy.layout_version_id != self.asset.layout_version_id:
            errors['access_policy'] = 'The policy must belong to the same layout version.'
        if self.lane_count < 1 or self.lane_spacing_m <= 0 or self.position_spacing_m <= 0:
            errors['lane_count'] = 'Lane count and lane/position spacing must be positive.'
        for field in ('lane_spacing_m', 'position_spacing_m', 'longitudinal_offset_m', 'lateral_offset_m'):
            if not math.isfinite(getattr(self, field)):
                errors[field] = 'Use a finite meter value.'
        if self.position_reference == 'che' and not self.reference_che_id:
            errors['reference_che'] = 'Equipment-relative positions require a reference CHE.'
        if self.position_reference == 'fixed' and self.reference_che_id:
            errors['reference_che'] = 'Fixed positions cannot reference a CHE.'
        if self.reference_che_id and self.reference_che.location_id != self.asset.layout_version.location_id:
            errors['reference_che'] = 'Reference equipment must belong to this terminal.'
        for start_name, end_name in [('bay_start', 'bay_end'), ('row_start', 'row_end')]:
            start, end = getattr(self, start_name), getattr(self, end_name)
            if start is not None and end is not None and start > end:
                errors[end_name] = 'The end of a range must be greater than or equal to its start.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.asset.code} / {self.name}'


class InterfacePosition(models.Model):
    transfer_interface = models.ForeignKey(
        TransferInterface, related_name='positions', on_delete=models.CASCADE,
    )
    sequence = models.PositiveIntegerField()
    lane_index = models.PositiveSmallIntegerField(default=1)
    position_index = models.PositiveIntegerField(default=1)
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=100)
    latitude = models.FloatField()
    longitude = models.FloatField()
    heading_deg = models.FloatField(default=0.0)
    capacity = models.PositiveIntegerField(default=1)
    is_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ['transfer_interface_id', 'sequence']
        constraints = [
            models.UniqueConstraint(
                fields=['transfer_interface', 'sequence'], name='unique_interface_position_sequence',
            ),
            models.UniqueConstraint(
                fields=['transfer_interface', 'code'], name='unique_interface_position_code',
            ),
            models.UniqueConstraint(
                fields=['transfer_interface', 'lane_index', 'position_index'], name='unique_interface_lane_position',
            ),
        ]

    def __str__(self):
        return f'{self.transfer_interface.code} / {self.name}'


class OperationalPosition(models.Model):
    YARD_SLOT = 'yard_slot'
    TRANSFER_POSITION = 'transfer_position'
    VESSEL_SLOT = 'vessel_slot'
    WAGON_SLOT = 'wagon_slot'
    VEHICLE_SLOT = 'vehicle_slot'
    CHASSIS_SLOT = 'chassis_slot'
    WORK_POINT = 'work_point'
    PARKING_POSITION = 'parking_position'
    POSITION_KIND_CHOICES = [
        (YARD_SLOT, 'Yard slot'),
        (TRANSFER_POSITION, 'Transfer position'),
        (VESSEL_SLOT, 'Vessel slot'),
        (WAGON_SLOT, 'Wagon slot'),
        (VEHICLE_SLOT, 'Vehicle slot'),
        (CHASSIS_SLOT, 'Chassis slot'),
        (WORK_POINT, 'Work point'),
        (PARKING_POSITION, 'Parking position'),
    ]

    layout_version = models.ForeignKey(
        LayoutVersion, related_name='operational_positions', on_delete=models.CASCADE,
    )
    code = models.CharField(max_length=80)
    name = models.CharField(max_length=120)
    position_kind = models.CharField(max_length=24, choices=POSITION_KIND_CHOICES)
    asset = models.ForeignKey(
        LayoutAsset, related_name='operational_positions', on_delete=models.CASCADE,
        blank=True, null=True,
    )
    stack_position = models.OneToOneField(
        Position, related_name='operational_position', on_delete=models.CASCADE,
        blank=True, null=True,
    )
    interface_position = models.OneToOneField(
        InterfacePosition, related_name='operational_position', on_delete=models.CASCADE,
        blank=True, null=True,
    )
    axis = models.ForeignKey(
        LinearAxis, related_name='operational_positions', on_delete=models.SET_NULL,
        blank=True, null=True,
    )
    chainage_m = models.FloatField(blank=True, null=True)
    latitude = models.FloatField(blank=True, null=True)
    longitude = models.FloatField(blank=True, null=True)
    elevation_m = models.FloatField(default=0.0)
    logical_address = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['code']
        constraints = [
            models.UniqueConstraint(
                fields=['layout_version', 'code'], name='unique_operational_position_code',
            )
        ]

    def clean(self):
        errors = {}
        if self.asset_id and self.asset.layout_version_id != self.layout_version_id:
            errors['asset'] = 'The asset must belong to the same layout version.'
        if self.axis_id and self.axis.asset.layout_version_id != self.layout_version_id:
            errors['axis'] = 'The axis must belong to the same layout version.'
        if self.stack_position_id and self.stack_position.pile.storage_grid.layout_asset.layout_version_id != self.layout_version_id:
            errors['stack_position'] = 'The yard slot must belong to the same layout version.'
        if self.interface_position_id and self.interface_position.transfer_interface.asset.layout_version_id != self.layout_version_id:
            errors['interface_position'] = 'The interface position must belong to the same layout version.'
        if (self.latitude is None) != (self.longitude is None):
            errors['latitude'] = 'Latitude and longitude must be supplied together.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.code


class CheOperatingScope(models.Model):
    che = models.ForeignKey(
        'Che', related_name='operating_scopes', on_delete=models.CASCADE,
        blank=True, null=True,
    )
    equipment_type = models.ForeignKey(
        'EquipmentType', related_name='operating_scopes', on_delete=models.CASCADE,
        blank=True, null=True,
    )
    asset = models.ForeignKey(
        LayoutAsset, related_name='che_operating_scopes', on_delete=models.CASCADE,
        blank=True, null=True,
    )
    axis = models.ForeignKey(
        LinearAxis, related_name='che_operating_scopes', on_delete=models.CASCADE,
        blank=True, null=True,
    )
    service_lane_axis = models.ForeignKey(
        LinearAxis, related_name='service_lane_scopes', on_delete=models.SET_NULL,
        blank=True, null=True,
        help_text='Road lane used by the scoped equipment while serving this area.',
    )
    min_chainage_m = models.FloatField(blank=True, null=True)
    max_chainage_m = models.FloatField(blank=True, null=True)
    bay_start = models.PositiveIntegerField(blank=True, null=True)
    bay_end = models.PositiveIntegerField(blank=True, null=True)
    row_start = models.PositiveIntegerField(blank=True, null=True)
    row_end = models.PositiveIntegerField(blank=True, null=True)
    can_reposition = models.BooleanField(default=False)
    is_enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(Q(che__isnull=False, equipment_type__isnull=True) | Q(che__isnull=True, equipment_type__isnull=False)),
                name='scope_exactly_one_che_subject',
            ),
            models.CheckConstraint(
                check=(Q(asset__isnull=False) | Q(axis__isnull=False)),
                name='scope_requires_asset_or_axis',
            ),
        ]

    def clean(self):
        errors = {}
        topology = self.asset or (self.axis.asset if self.axis_id else None)
        if self.asset_id and self.axis_id and self.asset.layout_version_id != self.axis.asset.layout_version_id:
            errors['axis'] = 'Scope asset and axis must belong to the same layout version.'
        if self.service_lane_axis_id:
            if self.service_lane_axis.axis_kind != LinearAxis.ROAD_CENTERLINE:
                errors['service_lane_axis'] = 'The service lane must be a road-centerline axis.'
            elif topology and self.service_lane_axis.asset.layout_version_id != topology.layout_version_id:
                errors['service_lane_axis'] = 'The service lane must belong to the same layout version.'
            elif topology and self.service_lane_axis.asset_id != topology.id:
                errors['service_lane_axis'] = 'The service lane must belong to the scoped layout asset.'
        if self.min_chainage_m is not None and self.max_chainage_m is not None and self.min_chainage_m > self.max_chainage_m:
            errors['max_chainage_m'] = 'Maximum chainage must be at least minimum chainage.'
        if errors:
            raise ValidationError(errors)


class RouteNode(models.Model):
    JUNCTION = 'junction'
    AXIS_POINT = 'axis_point'
    INTERFACE = 'interface'
    OPERATIONAL = 'operational'
    ENTRY_EXIT = 'entry_exit'
    NODE_KIND_CHOICES = [
        (JUNCTION, 'Junction'),
        (AXIS_POINT, 'Axis point'),
        (INTERFACE, 'Transfer interface'),
        (OPERATIONAL, 'Operational position'),
        (ENTRY_EXIT, 'Entry or exit'),
    ]

    layout_version = models.ForeignKey(
        LayoutVersion, related_name='route_nodes', on_delete=models.CASCADE,
    )
    code = models.CharField(max_length=80)
    name = models.CharField(max_length=120)
    node_kind = models.CharField(max_length=16, choices=NODE_KIND_CHOICES, default=JUNCTION)
    point = models.JSONField(help_text='GeoJSON Point geometry using WGS-84 coordinates.')
    asset = models.ForeignKey(
        LayoutAsset, related_name='route_nodes', on_delete=models.CASCADE,
        blank=True, null=True,
    )
    operational_position = models.OneToOneField(
        OperationalPosition, related_name='route_node', on_delete=models.CASCADE,
        blank=True, null=True,
    )
    transfer_interface = models.OneToOneField(
        TransferInterface, related_name='route_node', on_delete=models.CASCADE,
        blank=True, null=True,
    )
    is_generated = models.BooleanField(default=False)
    is_enabled = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['code']
        constraints = [
            models.UniqueConstraint(
                fields=['layout_version', 'code'], name='unique_route_node_code',
            )
        ]

    def clean(self):
        if self.asset_id and self.asset.layout_version_id != self.layout_version_id:
            raise ValidationError({'asset': 'The asset must belong to the same layout version.'})
        if self.operational_position_id and self.operational_position.layout_version_id != self.layout_version_id:
            raise ValidationError({'operational_position': 'The position must belong to the same layout version.'})
        if self.transfer_interface_id and self.transfer_interface.layout_version.pk != self.layout_version_id:
            raise ValidationError({'transfer_interface': 'The interface must belong to the same layout version.'})

    def __str__(self):
        return self.code


class RouteEdge(models.Model):
    TRAVEL = 'travel'
    HANDLE = 'handle'
    HANDOFF = 'handoff'
    COUPLE = 'couple'
    UNCOUPLE = 'uncouple'
    WAIT = 'wait'
    EDGE_KIND_CHOICES = [
        (TRAVEL, 'Travel'), (HANDLE, 'Handle'), (HANDOFF, 'Handoff'),
        (COUPLE, 'Couple'), (UNCOUPLE, 'Uncouple'), (WAIT, 'Wait'),
    ]
    ROAD = 'road'
    FREE_DRIVE = 'free_drive'
    CRANE = 'crane'
    RAIL = 'rail'
    WATER = 'water'
    STATIC = 'static'
    MOVEMENT_MODE_CHOICES = [
        (ROAD, 'Road'), (FREE_DRIVE, 'Free drive'), (CRANE, 'Crane'),
        (RAIL, 'Rail'), (WATER, 'Water'), (STATIC, 'Static transfer'),
    ]

    layout_version = models.ForeignKey(
        LayoutVersion, related_name='route_edges', on_delete=models.CASCADE,
    )
    code = models.CharField(max_length=100)
    name = models.CharField(max_length=140, blank=True, default='')
    from_node = models.ForeignKey(RouteNode, related_name='outgoing_edges', on_delete=models.CASCADE)
    to_node = models.ForeignKey(RouteNode, related_name='incoming_edges', on_delete=models.CASCADE)
    edge_kind = models.CharField(max_length=12, choices=EDGE_KIND_CHOICES, default=TRAVEL)
    movement_mode = models.CharField(max_length=16, choices=MOVEMENT_MODE_CHOICES)
    source_axis = models.ForeignKey(
        LinearAxis, related_name='route_edges', on_delete=models.CASCADE,
        blank=True, null=True,
    )
    access_policy = models.ForeignKey(
        AccessPolicy, related_name='route_edges', on_delete=models.SET_NULL,
        blank=True, null=True,
    )
    distance_m = models.FloatField(default=0.0)
    nominal_duration_seconds = models.FloatField(blank=True, null=True)
    capacity = models.PositiveIntegerField(default=1)
    base_cost = models.FloatField(default=1.0)
    is_generated = models.BooleanField(default=False)
    is_enabled = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['code']
        constraints = [
            models.UniqueConstraint(
                fields=['layout_version', 'code'], name='unique_route_edge_code',
            ),
            models.CheckConstraint(check=~Q(from_node=models.F('to_node')), name='route_edge_distinct_nodes'),
        ]

    def clean(self):
        errors = {}
        if self.from_node_id and self.from_node.layout_version_id != self.layout_version_id:
            errors['from_node'] = 'The node must belong to the edge layout version.'
        if self.to_node_id and self.to_node.layout_version_id != self.layout_version_id:
            errors['to_node'] = 'The node must belong to the edge layout version.'
        if self.source_axis_id and self.source_axis.asset.layout_version_id != self.layout_version_id:
            errors['source_axis'] = 'The axis must belong to the edge layout version.'
        if self.access_policy_id and self.access_policy.layout_version_id != self.layout_version_id:
            errors['access_policy'] = 'The policy must belong to the edge layout version.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.code


class RouteEdgeRequirement(models.Model):
    route_edge = models.ForeignKey(
        RouteEdge, related_name='requirements', on_delete=models.CASCADE,
    )
    component_type = models.ForeignKey(
        'ComponentType', related_name='route_edge_requirements', on_delete=models.RESTRICT,
    )
    role = models.CharField(max_length=40, blank=True, default='')
    minimum_quantity = models.PositiveSmallIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['route_edge', 'component_type', 'role'],
                name='unique_route_edge_component_requirement',
            )
        ]


class LayoutPreset(models.Model):
    code = models.CharField(max_length=50)
    version = models.PositiveIntegerField(default=1)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, default='')
    root_asset_kind = models.CharField(max_length=24, choices=LayoutAsset.ASSET_KIND_CHOICES)
    definition = models.JSONField(default=dict)
    is_system = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', '-version']
        constraints = [
            models.UniqueConstraint(fields=['code', 'version'], name='unique_layout_preset_version')
        ]

    def __str__(self):
        return f'{self.name} v{self.version}'


###################################
class WorkQueue(models.Model):
    name = models.CharField(max_length=30, unique=True)
    is_active = models.BooleanField(default=0)
    # equipment_pool
    description = models.CharField(max_length=30, unique=True, blank=True, null=True)

    current_user = models.ForeignKey(
        'auth.User',
        related_name='work_queue',
        on_delete=models.CASCADE, blank=True, null=True
    )

    location = models.ForeignKey(
        'Location',
        on_delete=models.RESTRICT, blank=True, null=True
    )

    def __str__(self):
        return self.name


###################################
class Order(models.Model):
    MOVE_IN = 'IN'
    MOVE_OUT = 'OT'
    HOUSEKEEPING = 'HK'
    SHIFT = 'SF'

    ORDER_TYPES = [
        (MOVE_IN, 'Move In'),
        (MOVE_OUT, 'Move Out'),
        (HOUSEKEEPING, 'Housekeeping'),
        (SHIFT, 'Shift'),
    ]

    DRAFT = 'draft'
    PLANNED = 'planned'
    RELEASED = 'released'
    IN_PROGRESS = 'in_progress'
    COMPLETE = 'complete'
    FAILED = 'failed'
    CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (DRAFT, 'Draft'),
        (PLANNED, 'Planned'),
        (RELEASED, 'Released'),
        (IN_PROGRESS, 'In progress'),
        (COMPLETE, 'Complete'),
        (FAILED, 'Failed'),
        (CANCELLED, 'Cancelled'),
    ]

    order_type = models.CharField(
        max_length=2,
        choices=ORDER_TYPES,
        default=HOUSEKEEPING,
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=DRAFT)
    priority = models.PositiveSmallIntegerField(
        default=100,
        validators=[MinValueValidator(1), MaxValueValidator(999)],
        help_text='Dispatch priority; lower values are dispatched first.',
    )
    container_number = models.ForeignKey(
        'ContainerVisit', related_name='container_visit', blank=True, null=True,
        on_delete=models.CASCADE,
    )
    work_queue = models.ForeignKey(
        'WorkQueue', related_name='orders', blank=True, null=True,
        on_delete=models.CASCADE,
    )
    ## expected time of completion
    ## actual time of completion
    etc = models.DateTimeField('expected time of completion', blank=True, null=True)
    atc = models.DateTimeField('actual time of completion', blank=True, null=True)
    released_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    from_position = models.ForeignKey(
        'OperationalPosition',
        on_delete=models.RESTRICT,
        related_name='from_position', blank=True, null=True
    )
    to_position = models.ForeignKey(
        'OperationalPosition',
        on_delete=models.RESTRICT,
        related_name='to_position', blank=True, null=True
    )

    def __str__(self):
        work_queue_name = self.work_queue.name if self.work_queue_id else "Unassigned"
        container_ref = self.container_number if self.container_number_id else "No Container"
        return f"{work_queue_name}-{self.pk}-{container_ref}"


class IntegrationInbox(models.Model):
    PENDING = 'pending'
    PROCESSED = 'processed'
    FAILED = 'failed'
    STATUS_CHOICES = [
        (PENDING, 'Pending'), (PROCESSED, 'Processed'), (FAILED, 'Failed'),
    ]

    event_id = models.UUIDField(primary_key=True)
    source = models.CharField(max_length=40)
    topic = models.CharField(max_length=300)
    schema = models.CharField(max_length=100)
    payload = models.JSONField()
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDING)
    error = models.TextField(blank=True, default='')
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['received_at']


class IntegrationOutbox(models.Model):
    event_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    topic = models.CharField(max_length=300)
    schema = models.CharField(max_length=100)
    payload = models.JSONField()
    qos = models.PositiveSmallIntegerField(default=1)
    retain = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['created_at']


###################################
class OrderRoutePlan(models.Model):
    DRAFT = 'draft'
    ACTIVE = 'active'
    SUPERSEDED = 'superseded'
    COMPLETED = 'completed'
    STATUS_CHOICES = [
        (DRAFT, 'Draft'), (ACTIVE, 'Active'),
        (SUPERSEDED, 'Superseded'), (COMPLETED, 'Completed'),
    ]

    order = models.ForeignKey(Order, related_name='route_plans', on_delete=models.CASCADE)
    layout_version = models.ForeignKey(
        LayoutVersion, related_name='order_route_plans', on_delete=models.RESTRICT,
    )
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=DRAFT)
    calculated_at = models.DateTimeField(auto_now_add=True)
    cost = models.FloatField(default=0.0)
    reason = models.TextField(blank=True, default='')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['order', 'version'], name='unique_route_plan_order_version')
        ]

    def __str__(self):
        return f'Order {self.order_id} route v{self.version}'


###################################
class OrderLeg(models.Model):
    MOVE = 'move'
    HANDLE = 'handle'
    HANDOFF = 'handoff'
    WAIT = 'wait'
    LEG_KIND_CHOICES = [
        (MOVE, 'Move'), (HANDLE, 'Handle'), (HANDOFF, 'Handoff'), (WAIT, 'Wait'),
    ]
    PLANNED = 'planned'
    DISPATCHED = 'dispatched'
    WORKING = 'working'
    COMPLETE = 'complete'
    FAILED = 'failed'
    BLOCKED = 'blocked'
    CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (PLANNED, 'Planned'), (DISPATCHED, 'Dispatched'), (WORKING, 'Working'),
        (COMPLETE, 'Complete'), (FAILED, 'Failed'), (BLOCKED, 'Blocked'),
        (CANCELLED, 'Cancelled'),
    ]

    order = models.ForeignKey(
        'Order', related_name='order_legs',
        on_delete=models.CASCADE, blank=True, null=True,
    )
    route_plan = models.ForeignKey(
        OrderRoutePlan, related_name='legs', on_delete=models.CASCADE,
        blank=True, null=True,
    )
    sequence = models.PositiveIntegerField(default=1)
    leg_kind = models.CharField(max_length=10, choices=LEG_KIND_CHOICES, default=MOVE)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PLANNED)
    che = models.ForeignKey(
        'Che',
        on_delete=models.RESTRICT, blank=True, null=True,
    )
    equipment_type = models.ForeignKey(
        'EquipmentType', related_name='order_legs', on_delete=models.RESTRICT,
        blank=True, null=True,
    )
    distance = models.FloatField(blank=True, null=True)
    start_position = models.ForeignKey(
        'OperationalPosition',
        on_delete=models.RESTRICT,
        related_name='starting_order_legs', blank=True, null=True
    )
    end_position = models.ForeignKey(
        'OperationalPosition',
        on_delete=models.RESTRICT,
        related_name='ending_order_legs', blank=True, null=True
    )
    planned_start = models.DateTimeField(blank=True, null=True)
    planned_end = models.DateTimeField(blank=True, null=True)
    actual_start = models.DateTimeField(blank=True, null=True)
    actual_end = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['order_id', 'sequence']
        constraints = [
            models.UniqueConstraint(
                fields=['route_plan', 'sequence'], condition=Q(route_plan__isnull=False),
                name='unique_order_leg_sequence_per_plan',
            )
        ]

    def __str__(self):
        if self.order_id:
            order_ref = self.order
            container_ref = self.order.container_number if self.order.container_number_id else "No Container"
            return f"{order_ref}-{self.id}-{container_ref}"
        return f"OrderLeg #{self.id}"


class OrderLegRouteEdge(models.Model):
    order_leg = models.ForeignKey(
        OrderLeg, related_name='route_edges', on_delete=models.CASCADE,
    )
    route_edge = models.ForeignKey(
        RouteEdge, related_name='order_leg_usages', on_delete=models.RESTRICT,
    )
    sequence = models.PositiveIntegerField()

    class Meta:
        ordering = ['order_leg_id', 'sequence']
        constraints = [
            models.UniqueConstraint(
                fields=['order_leg', 'sequence'], name='unique_route_edge_sequence_per_leg',
            )
        ]


###################################
class Che(models.Model):
    # Cached TIC4.0 power/activity facts plus MiniTOS operational assignment facts.
    powered_on = models.BooleanField(default=False)
    working = models.BooleanField(default=False)
    is_available = models.BooleanField(default=False)
    is_dispatched = models.BooleanField(default=False)
    is_in_maintenance = models.BooleanField(default=False)
    name = models.CharField(max_length=30, unique=True)
    equipment_type = models.ForeignKey(
        'EquipmentType',
        on_delete=models.CASCADE,
    )
    current_asset = models.ForeignKey(
        'LayoutAsset', related_name='current_ches',
        on_delete=models.SET_NULL, blank=True, null=True
    )
    current_user = models.ForeignKey(
        'auth.User',
        related_name='equipments',
        on_delete=models.RESTRICT, blank=True, null=True
    )
    # highlighted = models.TextField()

    latitude = models.FloatField(blank=True, null=True)
    longitude = models.FloatField(blank=True, null=True)
    rotation = models.FloatField(blank=True, null=False, default=0.0)  # Rotation in degrees, default to 0.0
    location = models.ForeignKey(
        'Location',
        on_delete=models.CASCADE,
    )

    last_maintenance = models.DateTimeField(blank=True, null=True)
    state_updated_at = models.DateTimeField(auto_now=True)
    # assigned_tasks: This would be a ManyToMany or ForeignKey to a Task/Order model

    def __str__(self):
        return self.name

    def _component_value(self, name, default):
        if not self.pk:
            return getattr(self, '_pending_component_values', {}).get(name, default)
        from .services.che_components import get_che_legacy_value
        return get_che_legacy_value(self, name, default)

    def _set_component_value(self, name, value):
        if not hasattr(self, '_pending_component_values'):
            self._pending_component_values = {}
        self._pending_component_values[name] = value

    @property
    def fuel_level(self):
        return self._component_value('fuel_level', 100.0)

    @fuel_level.setter
    def fuel_level(self, value):
        self._set_component_value('fuel_level', value)

    @property
    def twistlock_closed(self):
        return self._component_value('twistlock_closed', False)

    @twistlock_closed.setter
    def twistlock_closed(self, value):
        self._set_component_value('twistlock_closed', value)

    @property
    def running_hours(self):
        return self._component_value('running_hours', 0.0)

    @running_hours.setter
    def running_hours(self, value):
        self._set_component_value('running_hours', value)

    @property
    def running_hours_since_last_maintenance(self):
        return self._component_value('running_hours_since_last_maintenance', 0.0)

    @running_hours_since_last_maintenance.setter
    def running_hours_since_last_maintenance(self, value):
        self._set_component_value('running_hours_since_last_maintenance', value)


###################################
class EquipmentType(models.Model):
    name = models.CharField(max_length=30, unique=True)

    TT = 'TT'
    AVG = 'AVG'
    SC = 'SC'
    AS = 'AS'
    RS = 'RS'
    TH = 'TH'
    EH = 'EH'
    FL = 'FL'
    RTG = 'RTG'
    ARTG = 'ARTG'
    RGC = 'RGC'
    RMG = 'RMG'
    ARMG = 'ARMG'
    STS = 'STS'

    EQUIPMENT_TYPES = [
        (TT, 'Terminal Tractor'),
        (AVG, 'Automated Guided Vehicle'),
        (SC, 'Shuttle Carrier'),
        (AS, 'Auto Shuttle Carrier'),
        (RS, 'Reach Stacker'),
        (TH, 'Top Handler'),
        (EH, 'Empty Handler'),
        (FL, 'Forklift'),
        (RTG, 'RT Gantry'),
        (ARTG, 'Auto Rail Gantry'),
        (RGC, 'Rail Gantry Crane'),
        (RMG, 'Rail Mounted Gantry Crane'),
        (ARMG, 'Auto Rail Mounted Gantry Crane'),
        (STS, 'Ship to Shore'),
    ]
    ## Is Yard Equipment, Quay Equipment, Rail Equipment, Gate Equipment, Horizontal Transport
    is_horizontal_transport = models.BooleanField(default=False)
    ## If can move containers within the yard
    is_yard_equipment = models.BooleanField(default=False)
    ## If can move containers from or to a vessel, barge, or train
    is_quay_equipment = models.BooleanField(default=False)
    is_rail_equipment = models.BooleanField(default=False)
    ## if can move containers from or to a truck.
    is_gate_equipment = models.BooleanField(default=False)
    ## Equipment dimensions and capabilities
    length_m = models.FloatField(default=0.0)
    width_m = models.FloatField(default=0.0)
    height_m = models.FloatField(default=0.0)
    max_lift_weight_kg = models.FloatField(default=0.0)
    is_twin_capeable = models.BooleanField(default=False)

    ## Energy source: Diesel, Electric, Hybrid, etc.
    Diesel = 'Diesel'
    Electric = 'Electric'
    Hybrid = 'Hybrid'
    Gas = 'Gas'
    Other = 'Other'

    ENERGY_SOURCE_CHOICES = [
        (Diesel, 'Diesel'),
        (Electric, 'Electric'),
        (Hybrid, 'Hybrid'),
        (Gas, 'Gas'),
        (Other, 'Other'),
    ]
    energy_source = models.CharField(
        max_length=10,
        choices=ENERGY_SOURCE_CHOICES,
        default=Diesel,
    )
    ## Maintenance schedule and intervals
    minor_maintenance_interval_hours = models.IntegerField(default=100000)
    major_maintenance_interval_hours = models.IntegerField(default=500000)

    ## Autromated or manual operation
    is_automated = models.BooleanField(default=False)

    stacking_capacity = models.IntegerField(default=1)

    equipment_type = models.CharField(
        max_length=4,
        choices=EQUIPMENT_TYPES,
        default=SC,
    )

    def __str__(self):
        return self.name

    def _component_profile_value(self, name, default):
        if not self.pk:
            return getattr(self, '_pending_component_defaults', {}).get(name, default)
        from .services.che_components import get_equipment_type_legacy_value
        return get_equipment_type_legacy_value(self, name, default)

    def _set_component_profile_value(self, name, value):
        if not hasattr(self, '_pending_component_defaults'):
            self._pending_component_defaults = {}
        self._pending_component_defaults[name] = value

    @property
    def driving_speed_full_load_kmh(self):
        return self._component_profile_value('driving_speed_full_load_kmh', 0.0)

    @driving_speed_full_load_kmh.setter
    def driving_speed_full_load_kmh(self, value):
        self._set_component_profile_value('driving_speed_full_load_kmh', value)

    @property
    def driving_speed_empty_kmh(self):
        return self._component_profile_value('driving_speed_empty_kmh', 0.0)

    @driving_speed_empty_kmh.setter
    def driving_speed_empty_kmh(self, value):
        self._set_component_profile_value('driving_speed_empty_kmh', value)

    @property
    def lifting_speed_full_load_m_per_min(self):
        return self._component_profile_value('lifting_speed_full_load_m_per_min', 0.0)

    @lifting_speed_full_load_m_per_min.setter
    def lifting_speed_full_load_m_per_min(self, value):
        self._set_component_profile_value('lifting_speed_full_load_m_per_min', value)

    @property
    def lifting_speed_empty_m_per_min(self):
        return self._component_profile_value('lifting_speed_empty_m_per_min', 0.0)

    @lifting_speed_empty_m_per_min.setter
    def lifting_speed_empty_m_per_min(self, value):
        self._set_component_profile_value('lifting_speed_empty_m_per_min', value)

    @property
    def power_rating_kw(self):
        return self._component_profile_value('power_rating_kw', 0.0)

    @power_rating_kw.setter
    def power_rating_kw(self, value):
        self._set_component_profile_value('power_rating_kw', value)

    @property
    def engine_type(self):
        return self._component_profile_value('engine_type', None)

    @engine_type.setter
    def engine_type(self, value):
        self._set_component_profile_value('engine_type', value)

    @property
    def fuel_tank_capacity(self):
        return self._component_profile_value('fuel_tank_capacity', 0.0)

    @fuel_tank_capacity.setter
    def fuel_tank_capacity(self, value):
        self._set_component_profile_value('fuel_tank_capacity', value)

    @property
    def turning_radius_m(self):
        return self._component_profile_value('turning_radius_m', 0.0)

    @turning_radius_m.setter
    def turning_radius_m(self, value):
        self._set_component_profile_value('turning_radius_m', value)


###################################
class ComponentType(models.Model):
    CHE = 'che'
    DRIVE = 'drive'
    HOIST = 'hoist'
    TROLLEY = 'trolley'
    GANTRY = 'gantry'
    BOOM = 'boom'
    SPREADER = 'spreader'
    POWER_SOURCE = 'energysupply'
    ENERGY_STORE = 'energystoragesystem'
    OTHER = 'other'

    COMPONENT_TYPES = [
        (CHE, 'CHE'),
        (DRIVE, 'Drive'),
        (HOIST, 'Hoist'),
        (TROLLEY, 'Trolley'),
        (GANTRY, 'Gantry'),
        (BOOM, 'Boom'),
        (SPREADER, 'Spreader'),
        (POWER_SOURCE, 'Energy supply'),
        (ENERGY_STORE, 'Energy storage system'),
        (OTHER, 'Other'),
    ]

    code = models.CharField(max_length=24, choices=COMPONENT_TYPES, unique=True)
    name = models.CharField(max_length=60)
    tic40_subject = models.CharField(max_length=40, blank=True, default='')
    description = models.TextField(blank=True, default='')
    is_motion_component = models.BooleanField(default=False)
    is_energy_component = models.BooleanField(default=False)
    supports_multiple = models.BooleanField(default=False)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class EquipmentTypeComponent(models.Model):
    equipment_type = models.ForeignKey(
        'EquipmentType', related_name='component_profiles', on_delete=models.CASCADE
    )
    component_type = models.ForeignKey(
        'ComponentType', related_name='equipment_type_profiles', on_delete=models.RESTRICT
    )
    role = models.CharField(max_length=40)
    display_name = models.CharField(max_length=80)
    parent_role = models.CharField(max_length=40, blank=True, default='')
    is_required = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    quantity = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    default_configuration = models.JSONField(blank=True, default=dict)

    class Meta:
        ordering = ['equipment_type__name', 'role']
        constraints = [
            models.UniqueConstraint(
                fields=['equipment_type', 'role'], name='unique_equipment_type_component_role'
            ),
        ]

    def __str__(self):
        return f'{self.equipment_type.name}: {self.display_name}'


class CheComponent(models.Model):
    che = models.ForeignKey('Che', related_name='components', on_delete=models.CASCADE)
    component_type = models.ForeignKey(
        'ComponentType', related_name='installed_components', on_delete=models.RESTRICT
    )
    template = models.ForeignKey(
        'EquipmentTypeComponent', related_name='installed_components',
        on_delete=models.SET_NULL, blank=True, null=True,
    )
    parent = models.ForeignKey(
        'self', related_name='children', on_delete=models.SET_NULL, blank=True, null=True
    )
    role = models.CharField(max_length=40)
    name = models.CharField(max_length=80)
    manufacturer = models.CharField(max_length=80, blank=True, default='')
    model = models.CharField(max_length=80, blank=True, default='')
    serial_number = models.CharField(max_length=100, blank=True, default='')
    installed_at = models.DateTimeField(blank=True, null=True)
    removed_at = models.DateTimeField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(blank=True, default=dict)

    class Meta:
        ordering = ['che__name', 'role']
        constraints = [
            models.UniqueConstraint(fields=['che', 'role'], name='unique_che_component_role'),
        ]

    def __str__(self):
        return f'{self.che.name}: {self.name}'


class Engine(models.Model):
    component = models.OneToOneField(
        'CheComponent', related_name='engine', on_delete=models.CASCADE
    )
    engine_type = models.CharField(max_length=80, blank=True, default='')
    fuel_type = models.CharField(max_length=40, blank=True, default='diesel')
    rated_power_kw = models.FloatField(default=0.0, validators=[MinValueValidator(0.0)])
    nominal_rpm = models.FloatField(default=0.0, validators=[MinValueValidator(0.0)])
    running_hours = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    hours_since_maintenance = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def __str__(self):
        return f'Engine for {self.component}'


class Battery(models.Model):
    component = models.OneToOneField(
        'CheComponent', related_name='battery', on_delete=models.CASCADE
    )
    chemistry = models.CharField(max_length=40, blank=True, default='')
    capacity_kwh = models.FloatField(default=0.0, validators=[MinValueValidator(0.0)])
    nominal_voltage_v = models.FloatField(default=0.0, validators=[MinValueValidator(0.0)])
    max_charge_power_kw = models.FloatField(default=0.0, validators=[MinValueValidator(0.0)])
    max_discharge_power_kw = models.FloatField(default=0.0, validators=[MinValueValidator(0.0)])
    minimum_soc_percent = models.FloatField(
        default=0.0, validators=[MinValueValidator(0.0), MaxValueValidator(100.0)]
    )
    maximum_soc_percent = models.FloatField(
        default=100.0, validators=[MinValueValidator(0.0), MaxValueValidator(100.0)]
    )

    def __str__(self):
        return f'Battery for {self.component}'


class EnergyTank(models.Model):
    component = models.OneToOneField(
        'CheComponent', related_name='energy_tank', on_delete=models.CASCADE
    )
    fuel_type = models.CharField(max_length=40, blank=True, default='diesel')
    capacity_l = models.FloatField(default=0.0, validators=[MinValueValidator(0.0)])

    def __str__(self):
        return f'Energy tank for {self.component}'


class MotionAxis(models.Model):
    component = models.OneToOneField(
        'CheComponent', related_name='motion_axis', on_delete=models.CASCADE
    )
    maximum_speed_empty_m_s = models.FloatField(default=0.0, validators=[MinValueValidator(0.0)])
    maximum_speed_loaded_m_s = models.FloatField(default=0.0, validators=[MinValueValidator(0.0)])
    maximum_acceleration_m_s2 = models.FloatField(default=0.0, validators=[MinValueValidator(0.0)])
    maximum_deceleration_m_s2 = models.FloatField(default=0.0, validators=[MinValueValidator(0.0)])
    motion_deadband_m_s = models.FloatField(default=0.01, validators=[MinValueValidator(0.0)])
    minimum_position_m = models.FloatField(blank=True, null=True)
    maximum_position_m = models.FloatField(blank=True, null=True)
    turning_radius_m = models.FloatField(default=0.0, validators=[MinValueValidator(0.0)])
    cumulative_motion_hours = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cumulative_distance_m = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    def __str__(self):
        return f'Motion axis for {self.component}'


class Spreader(models.Model):
    component = models.OneToOneField(
        'CheComponent', related_name='spreader', on_delete=models.CASCADE
    )
    twin_capable = models.BooleanField(default=False)
    tandem_capable = models.BooleanField(default=False)
    supported_sizes_ft = models.JSONField(blank=True, default=list)
    current_size_ft = models.PositiveSmallIntegerField(blank=True, null=True)
    twistlocks_locked = models.BooleanField(default=False)
    landed = models.BooleanField(default=False)
    container_present = models.BooleanField(default=False)

    def __str__(self):
        return f'Spreader for {self.component}'


class SignalDefinition(models.Model):
    BOOLEAN = 'boolean'
    INTEGER = 'integer'
    FLOAT = 'float'
    STRING = 'string'
    JSON = 'json'
    DATA_TYPES = [
        (BOOLEAN, 'Boolean'),
        (INTEGER, 'Integer'),
        (FLOAT, 'Float'),
        (STRING, 'String'),
        (JSON, 'JSON'),
    ]

    CURRENT = 'current'
    STATE = 'state'
    CUMULATIVE = 'cumulative'
    EVENT = 'event'
    VALUE_KINDS = [
        (CURRENT, 'Current value'),
        (STATE, 'State'),
        (CUMULATIVE, 'Cumulative value'),
        (EVENT, 'Event'),
    ]

    component_type = models.ForeignKey(
        'ComponentType', related_name='signal_definitions', on_delete=models.CASCADE
    )
    code = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=100)
    tic40_path = models.CharField(max_length=240, blank=True, default='')
    data_type = models.CharField(max_length=12, choices=DATA_TYPES)
    unit = models.CharField(max_length=30, blank=True, default='')
    value_kind = models.CharField(max_length=12, choices=VALUE_KINDS, default=CURRENT)
    standard_version = models.CharField(max_length=20, blank=True, default='2026.019')
    description = models.TextField(blank=True, default='')
    is_required_for_oee = models.BooleanField(default=False)

    class Meta:
        ordering = ['component_type__name', 'code']

    def __str__(self):
        return self.code


class SignalBinding(models.Model):
    SPARKPLUG = 'sparkplug'
    MQTT = 'mqtt'
    REST = 'rest'
    SIMULATOR = 'simulator'
    MANUFACTURER = 'manufacturer'
    MANUAL = 'manual'
    SOURCE_PROTOCOLS = [
        (SPARKPLUG, 'Sparkplug B'),
        (MQTT, 'MQTT'),
        (REST, 'REST API'),
        (SIMULATOR, 'Simulator'),
        (MANUFACTURER, 'Manufacturer interface'),
        (MANUAL, 'Manual'),
    ]

    component = models.ForeignKey(
        'CheComponent', related_name='signal_bindings', on_delete=models.CASCADE
    )
    signal_definition = models.ForeignKey(
        'SignalDefinition', related_name='bindings', on_delete=models.RESTRICT
    )
    source_protocol = models.CharField(
        max_length=16, choices=SOURCE_PROTOCOLS, default=SIMULATOR
    )
    source_metric = models.CharField(max_length=160)
    source_topic = models.CharField(max_length=240, blank=True, default='')
    scale_factor = models.FloatField(default=1.0)
    offset = models.FloatField(default=0.0)
    is_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ['component__che__name', 'signal_definition__code']
        constraints = [
            models.UniqueConstraint(
                fields=['component', 'signal_definition'], name='unique_component_signal_binding'
            ),
        ]

    def __str__(self):
        return f'{self.component}: {self.signal_definition.code}'


class CurrentSignalValue(models.Model):
    GOOD = 'good'
    UNCERTAIN = 'uncertain'
    BAD = 'bad'
    STALE = 'stale'
    QUALITIES = [
        (GOOD, 'Good'),
        (UNCERTAIN, 'Uncertain'),
        (BAD, 'Bad'),
        (STALE, 'Stale'),
    ]

    binding = models.OneToOneField(
        'SignalBinding', related_name='current_value', on_delete=models.CASCADE
    )
    value = models.JSONField(blank=True, null=True)
    source_timestamp = models.DateTimeField(blank=True, null=True)
    recorded_at = models.DateTimeField(auto_now=True)
    quality = models.CharField(max_length=10, choices=QUALITIES, default=GOOD)
    sequence = models.PositiveBigIntegerField(blank=True, null=True)

    def __str__(self):
        return f'{self.binding.signal_definition.code}={self.value}'


class OperationalStateInterval(models.Model):
    OFF = 'off'
    IDLE = 'idle'
    WORKING = 'working'
    FAULT = 'fault'
    MAINTENANCE = 'maintenance'
    UNKNOWN = 'unknown'
    STATES = [
        (OFF, 'Off'),
        (IDLE, 'Idle'),
        (WORKING, 'Working'),
        (FAULT, 'Fault'),
        (MAINTENANCE, 'Maintenance'),
        (UNKNOWN, 'Unknown'),
    ]

    che = models.ForeignKey('Che', related_name='state_intervals', on_delete=models.CASCADE)
    state = models.CharField(max_length=12, choices=STATES)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(blank=True, null=True)
    source = models.CharField(max_length=40, blank=True, default='derived')
    reason = models.CharField(max_length=240, blank=True, default='')
    confidence = models.FloatField(
        default=1.0, validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )

    class Meta:
        ordering = ['-started_at']
        constraints = [
            models.UniqueConstraint(
                fields=['che'], condition=Q(ended_at__isnull=True),
                name='one_open_operational_state_per_che',
            ),
        ]

    def __str__(self):
        return f'{self.che.name}: {self.get_state_display()}'


class HandlingCycle(models.Model):
    IN_PROGRESS = 'in_progress'
    COMPLETED = 'completed'
    FAILED = 'failed'
    ABORTED = 'aborted'
    RESULTS = [
        (IN_PROGRESS, 'In progress'),
        (COMPLETED, 'Completed'),
        (FAILED, 'Failed'),
        (ABORTED, 'Aborted'),
    ]

    che = models.ForeignKey('Che', related_name='handling_cycles', on_delete=models.CASCADE)
    order_leg = models.ForeignKey(
        'OrderLeg', related_name='handling_cycles', on_delete=models.SET_NULL,
        blank=True, null=True,
    )
    container_visit = models.ForeignKey(
        'ContainerVisit', related_name='handling_cycles', on_delete=models.SET_NULL,
        blank=True, null=True,
    )
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(blank=True, null=True)
    result = models.CharField(max_length=12, choices=RESULTS, default=IN_PROGRESS)
    ideal_cycle_seconds = models.PositiveIntegerField(blank=True, null=True)
    start_latitude = models.FloatField(blank=True, null=True)
    start_longitude = models.FloatField(blank=True, null=True)
    end_latitude = models.FloatField(blank=True, null=True)
    end_longitude = models.FloatField(blank=True, null=True)
    metadata = models.JSONField(blank=True, default=dict)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f'{self.che.name} cycle {self.pk or "new"}'



###################################
class CarrierService(models.Model):
    MONDAY = 'Monday'
    TUESDAY = 'Tuesday'
    WEDNESDAY = 'Wednesday'
    THURSDAY = 'Thursday'
    FRIDAY = 'Friday'
    SATURDAY = 'Saturday'
    SUNDAY = 'Sunday'

    WEEKDAYS = [
        (MONDAY, 'Monday'),
        (TUESDAY, 'Tuesday'),
        (WEDNESDAY, 'Wednesday'),
        (THURSDAY, 'Thursday'),
        (FRIDAY, 'Friday'),
        (SATURDAY, 'Saturday'),
        (SUNDAY, 'Sunday'),
    ]

    MONTHLY_PATTERN_DAY = 'Day'
    MONTHLY_PATTERN_SUNDAY = 'Sunday'
    MONTHLY_PATTERN_MONDAY = 'Monday'
    MONTHLY_PATTERN_TUESDAY = 'Tuesday'
    MONTHLY_PATTERN_WEDNESDAY = 'Wednesday'
    MONTHLY_PATTERN_THURSDAY = 'Thursday'
    MONTHLY_PATTERN_FRIDAY = 'Friday'
    MONTHLY_PATTERN_SATURDAY = 'Saturday'

    MONTHLY_PATTERNS = [
        (MONTHLY_PATTERN_DAY, 'Day'),
        (MONTHLY_PATTERN_SUNDAY, 'Sunday'),
        (MONTHLY_PATTERN_MONDAY, 'Monday'),
        (MONTHLY_PATTERN_TUESDAY, 'Tuesday'),
        (MONTHLY_PATTERN_WEDNESDAY, 'Wednesday'),
        (MONTHLY_PATTERN_THURSDAY, 'Thursday'),
        (MONTHLY_PATTERN_FRIDAY, 'Friday'),
        (MONTHLY_PATTERN_SATURDAY, 'Saturday'),
    ]

    WEEKMONTH_ORDINALS = [
        ('First', 'First'),
        ('Second', 'Second'),
        ('Third', 'Third'),
        ('Fourth', 'Fourth'),
        ('Last', 'Last'),
    ]

    END_ON_THIS_DAY = 'on this day'
    END_AFTER = 'after'
    END_NEVER = 'no end date'

    END_CHOICES = [
        (END_ON_THIS_DAY, 'On this day'),
        (END_AFTER, 'After'),
        (END_NEVER, 'No end date'),
    ]

    REPEAT_DAILY = 'Daily'
    REPEAT_WEEKLY = 'Weekly'
    REPEAT_MONTHLY = 'Monthly'
    REPEAT_YEARLY = 'Yearly'

    REPEAT_CHOICES = [
        (REPEAT_DAILY, 'Daily'),
        (REPEAT_WEEKLY, 'Weekly'),
        (REPEAT_MONTHLY, 'Monthly'),
        (REPEAT_YEARLY, 'Yearly'),
    ]

    name = models.CharField(max_length=50, unique=True)
    location = models.ForeignKey(
        'Location',
        on_delete=models.RESTRICT,
        related_name='carrier_services'
    )

    VESSEL = 'Vessel'
    BARGE = 'Barge'
    TRUCK = 'Truck'
    TRAIN = 'Train'

    VISIT_TYPES = [
        (VESSEL, 'Vessel'),
        (BARGE, 'Barge'),
        (TRUCK, 'Truck'),
        (TRAIN, 'Train'),
    ]

    name = models.CharField(max_length=30, unique=True)
    carrier_service_type = models.CharField(
        max_length=10,
        choices=VISIT_TYPES,
        default=VESSEL,
    )

    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    end_type = models.CharField(max_length=15, choices=END_CHOICES, default=END_NEVER)
    end_after_occurrences = IntegerRangeField(min_value=1, max_value=100, blank=True, null=True)

    repeat = models.CharField(max_length=10, choices=REPEAT_CHOICES, default=REPEAT_DAILY)
    repeat_daily = IntegerRangeField(min_value=1, max_value=100, blank=True, null=True)
    repeat_weekly = IntegerRangeField(min_value=1, max_value=100, blank=True, null=True)
    on_weekdays = models.CharField(max_length=100, blank=True, null=True, help_text='Comma-separated weekdays')
    repeat_monthly = IntegerRangeField(min_value=1, max_value=100, blank=True, null=True)
    repeat_yearly = models.IntegerField(blank=True, null=True, help_text='Month number 1-12')

    on_monthly_pattern = models.CharField(max_length=10, choices=MONTHLY_PATTERNS, blank=True, null=True)
    on_monthly_day = IntegerRangeField(min_value=1, max_value=31, blank=True, null=True)
    on_monthly_week = models.CharField(max_length=6, choices=WEEKMONTH_ORDINALS, blank=True, null=True)

    window_start_time = models.TimeField(blank=True, null=True)
    window_end_time = models.TimeField(blank=True, null=True)

    window_start_weekday = models.CharField(
        max_length=10,
        choices=WEEKDAYS,
        blank=True,
        null=True
    )
    window_end_weekday = models.CharField(
        max_length=10,
        choices=WEEKDAYS,
        blank=True,
        null=True
    )

    window_start_monthday = models.IntegerField(blank=True, null=True)
    window_end_monthday = models.IntegerField(blank=True, null=True)

    moves_in = models.IntegerField(default=0)
    moves_out = models.IntegerField(default=0)
    vessel_length_m = models.FloatField(
        blank=True, null=True,
        help_text='Length overall in meters for vessel and barge services',
    )

    default_berth = models.ForeignKey(
        'BerthSegment', on_delete=models.SET_NULL, related_name='default_services',
        blank=True, null=True,
    )
    default_start_bollard = models.ForeignKey(
        'LinearMarker', on_delete=models.SET_NULL, related_name='services_starting_here',
        blank=True, null=True,
    )
    default_end_bollard = models.ForeignKey(
        'LinearMarker', on_delete=models.SET_NULL, related_name='services_ending_here',
        blank=True, null=True,
    )

    primary_equipment_type = models.ForeignKey(
        'EquipmentType', on_delete=models.SET_NULL,
        related_name='primary_carrier_services', blank=True, null=True,
        help_text='Primary entry/exit equipment used to calculate service productivity',
    )
    crane_split = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal('1.00'),
        validators=[MinValueValidator(Decimal('0.01'))],
        help_text='Fractional number of primary equipment units assigned',
    )
    expected_productivity_per_crane = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('25.00'),
        validators=[MinValueValidator(Decimal('0.01'))],
        help_text='Expected moves per hour for each primary equipment unit',
    )
    horizontal_transport_per_crane = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text='Horizontal transport units required per primary equipment unit',
    )

    periodicity = models.CharField(max_length=20, choices=[
        ('Daily', 'Daily'),
        ('Weekly', 'Weekly'),
        ('Bi-weekly', 'Bi-weekly'),
        ('Monthly', 'Monthly'),
    ], default='Weekly')

    def __str__(self):
        return self.name

    @property
    def expected_productivity_per_hour(self):
        return self.crane_split * self.expected_productivity_per_crane

    @property
    def calculated_duration_minutes(self):
        return calculated_operation_minutes(
            self.moves_in + self.moves_out,
            self.crane_split,
            self.expected_productivity_per_crane,
        )

    @property
    def calculated_duration_display(self):
        return format_duration_minutes(self.calculated_duration_minutes)


class CarrierServiceEquipmentRequirement(models.Model):
    carrier_service = models.ForeignKey(
        CarrierService, on_delete=models.CASCADE, related_name='equipment_requirements'
    )
    equipment_type = models.ForeignKey(
        EquipmentType, on_delete=models.RESTRICT, related_name='service_requirements'
    )
    equipment_split = models.DecimalField(
        max_digits=6, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    expected_productivity_per_hour = models.DecimalField(
        max_digits=8, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    horizontal_transport_per_unit = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['carrier_service', 'equipment_type'],
                name='unique_equipment_requirement_per_service',
            )
        ]

    def __str__(self):
        return f'{self.carrier_service.name}: {self.equipment_type.name}'


###################################
class CarrierVisit(models.Model):
    PLANNED = 'Created'
    ANNOUNCED = 'Announced'
    ARRIVED = 'Arrived'
    IN_OPERATION = 'InOperation'
    COMPLETE = 'Complete'
    DEPARTED = 'Departed'

    STATUS_CHOICES = [
        (PLANNED, 'Created'),
        (ANNOUNCED, 'Announced'),
        (ARRIVED, 'Arrived'),
        (IN_OPERATION, 'In Operation'),
        (COMPLETE, 'Complete'),
        (DEPARTED, 'Departed'),
    ]

    VESSEL = 'Vessel'
    BARGE = 'Barge'
    TRUCK = 'Truck'
    TRAIN = 'Train'

    VISIT_TYPES = [
        (VESSEL, 'Vessel'),
        (BARGE, 'Barge'),
        (TRUCK, 'Truck'),
        (TRAIN, 'Train'),
    ]

    name = models.CharField(max_length=30, unique=True)
    visit_type = models.CharField(
        max_length=10,
        choices=VISIT_TYPES,
        default=VESSEL,
    )
    status = models.CharField(
        max_length=15,
        choices=STATUS_CHOICES,
        default=PLANNED,
    )
    location = models.ForeignKey(
        'Location',
        on_delete=models.RESTRICT,
    )
    eta = models.DateTimeField(blank=True, null=True)
    etd = models.DateTimeField(blank=True, null=True)
    ata = models.DateTimeField(blank=True, null=True)
    atd = models.DateTimeField(blank=True, null=True)
    vessel_name = models.CharField(max_length=50, blank=True, null=True)
    voyage_number = models.CharField(max_length=20, blank=True, null=True)
    carrier = models.CharField(max_length=50, blank=True, null=True)
    berth = models.CharField(max_length=10, blank=True, null=True)
    planned_berth = models.ForeignKey(
        'BerthSegment', on_delete=models.SET_NULL, related_name='carrier_visits',
        blank=True, null=True,
    )
    berth_start_bollard = models.ForeignKey(
        'LinearMarker', on_delete=models.SET_NULL, related_name='visits_starting_here',
        blank=True, null=True,
    )
    berth_end_bollard = models.ForeignKey(
        'LinearMarker', on_delete=models.SET_NULL, related_name='visits_ending_here',
        blank=True, null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)

    # Reference to the CarrierService this visit is associated with
    carrier_service = models.ForeignKey(
        'CarrierService',
        on_delete=models.RESTRICT,
        blank=True,
        null=True,
    )
    window_start = models.DateTimeField(blank=True, null=True)
    ops_start = models.DateTimeField(blank=True, null=True)
    ops_end = models.DateTimeField(blank=True, null=True)
    window_end = models.DateTimeField(blank=True, null=True)
    moves_in = models.IntegerField(default=0)
    moves_out = models.IntegerField(default=0)
    moves_import = models.IntegerField(default=0)
    moves_export = models.IntegerField(default=0)
    moves_ts = models.IntegerField(default=0)
    moves_restow = models.IntegerField(default=0)
    total_moves = models.IntegerField(default=0)

    primary_equipment_type = models.ForeignKey(
        'EquipmentType', on_delete=models.SET_NULL,
        related_name='primary_carrier_visits', blank=True, null=True,
    )
    crane_split = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal('1.00'),
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    expected_productivity_per_crane = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('25.00'),
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    horizontal_transport_per_crane = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )

    def __str__(self):
        return f"{self.name} ({self.status})"

    @property
    def expected_productivity_per_hour(self):
        return self.crane_split * self.expected_productivity_per_crane

    @property
    def calculated_duration_minutes(self):
        return calculated_operation_minutes(
            self.total_moves or self.moves_in + self.moves_out,
            self.crane_split,
            self.expected_productivity_per_crane,
        )

    @property
    def calculated_duration_display(self):
        return format_duration_minutes(self.calculated_duration_minutes)

    def save(self, *args, **kwargs):
        duration_minutes = self.calculated_duration_minutes
        if self.eta and duration_minutes is not None:
            self.etd = self.eta + timedelta(minutes=duration_minutes)
        super().save(*args, **kwargs)


class CarrierVisitEquipmentRequirement(models.Model):
    carrier_visit = models.ForeignKey(
        CarrierVisit, on_delete=models.CASCADE, related_name='equipment_requirements'
    )
    equipment_type = models.ForeignKey(
        EquipmentType, on_delete=models.RESTRICT, related_name='visit_requirements'
    )
    equipment_split = models.DecimalField(
        max_digits=6, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    expected_productivity_per_hour = models.DecimalField(
        max_digits=8, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    horizontal_transport_per_unit = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['carrier_visit', 'equipment_type'],
                name='unique_equipment_requirement_per_visit',
            )
        ]

    def __str__(self):
        return f'{self.carrier_visit.name}: {self.equipment_type.name}'


###################################
class POD(models.Model):
    name = models.CharField(max_length=30, unique=True)
    uncode = models.CharField(max_length=5, unique=True)
    country = models.CharField(max_length=30)

    def __str__(self):
        return self.name


###################################
class ServiceCargoRoute(models.Model):
    FCL = 'FCL'
    MTY = 'MTY'

    CONTAINER_TYPES = [
        (FCL, 'Full Container Load'),
        (MTY, 'Empty Container'),
    ]

    inbound_carrier_service = models.ForeignKey(
        'CarrierService',
        on_delete=models.CASCADE,
        related_name='outbound_cargo_routes'
    )
    outbound_carrier_service = models.ForeignKey(
        'CarrierService',
        on_delete=models.CASCADE,
        related_name='inbound_cargo_routes'
    )
    pod = models.ForeignKey(
        'POD',
        on_delete=models.RESTRICT,
        blank=True,
        null=True,
    )
    container_type = models.CharField(
        max_length=3,
        choices=CONTAINER_TYPES,
        default=FCL,
    )
    iso_type = models.ForeignKey(
        'ISOType',
        on_delete=models.RESTRICT,
    )
    hazard_codes = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text='Comma-separated IMDG hazard codes'
    )
    is_reefer = models.BooleanField(default=False)
    container_count = models.IntegerField(default=0)
    expected_dwell_time_days = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.0,
        help_text='Expected dwell time in days'
    )

    class Meta:
        unique_together = ('inbound_carrier_service', 'outbound_carrier_service', 'pod', 'container_type', 'iso_type')

    def __str__(self):
        return f"{self.inbound_carrier_service.name} → {self.outbound_carrier_service.name} ({self.container_type})"


###################################
class VisitCargoRoute(models.Model):
    FCL = 'FCL'
    MTY = 'MTY'

    CONTAINER_TYPES = [
        (FCL, 'Full Container Load'),
        (MTY, 'Empty Container'),
    ]

    inbound_carrier_visit = models.ForeignKey(
        'CarrierVisit',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='outbound_visit_cargo_routes'
    )
    inbound_carrier_service = models.ForeignKey(
        'CarrierService',
        on_delete=models.CASCADE,
        related_name='inbound_service_cargo_routes'
    )
    outbound_carrier_visit = models.ForeignKey(
        'CarrierVisit',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='inbound_visit_cargo_routes'
    )
    outbound_carrier_service = models.ForeignKey(
        'CarrierService',
        on_delete=models.CASCADE,
        related_name='outbound_service_cargo_routes'
    )
    pod = models.ForeignKey(
        'POD',
        on_delete=models.RESTRICT,
        blank=True,
        null=True,
    )
    container_type = models.CharField(
        max_length=3,
        choices=CONTAINER_TYPES,
        default=FCL,
    )
    iso_type = models.ForeignKey(
        'ISOType',
        on_delete=models.RESTRICT,
    )
    hazard_codes = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text='Comma-separated IMDG hazard codes'
    )
    is_reefer = models.BooleanField(default=False)
    container_count = models.IntegerField(default=0)
    expected_dwell_time_days = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.0,
        help_text='Expected dwell time in days'
    )

    class Meta:
        unique_together = ('inbound_carrier_visit', 'outbound_carrier_visit', 'outbound_carrier_service', 'pod', 'container_type', 'iso_type')

    def __str__(self):
        visit_name = self.inbound_carrier_visit.name if self.inbound_carrier_visit_id else "No Inbound Visit"
        service_name = self.outbound_carrier_service.name if self.outbound_carrier_service_id else "No Outbound Service"
        return f"{visit_name} → {service_name} ({self.container_type})"


###################################
class PileBooking(models.Model):
    pile = models.ForeignKey(
        'Pile',
        on_delete=models.CASCADE,
        related_name='bookings'
    )
    visit_cargo_route = models.ForeignKey(
        'VisitCargoRoute',
        on_delete=models.CASCADE,
        related_name='pile_bookings'
    )
    tier_limit = IntegerRangeField(min_value=1, max_value=20)
    start_booking_time = models.DateTimeField()
    end_booking_time = models.DateTimeField()

    class Meta:
        unique_together = ('pile', 'visit_cargo_route')

    def __str__(self):
        return f"{self.pile} → {self.visit_cargo_route} (Tier {self.tier_limit})"


###################################
class PointOfWork(models.Model):
    TERMINAL_AREA = 'terminal area'
    ENTRY_POINT = 'entry point'
    DEPARTURE_POINT = 'departure point'
    ENTRY_DEPARTURE_POINT = 'entry and departure point'

    POW_TYPE_CHOICES = [
        (TERMINAL_AREA, 'Terminal Area'),
        (ENTRY_POINT, 'Entry Point'),
        (DEPARTURE_POINT, 'Departure Point'),
        (ENTRY_DEPARTURE_POINT, 'Entry and Departure Point'),
    ]

    name = models.CharField(max_length=100)
    pow_type = models.CharField(
        max_length=25,
        choices=POW_TYPE_CHOICES,
        default=TERMINAL_AREA,
    )
    related_ches = models.ManyToManyField(
        'Che',
        related_name='points_of_work',
        blank=True,
    )
    location = models.ForeignKey(
        'Location',
        on_delete=models.CASCADE,
        related_name='points_of_work'
    )
    code = models.CharField(max_length=20)
    latitude = models.FloatField(blank=True, null=True)
    longitude = models.FloatField(blank=True, null=True)
    operational_position = models.ForeignKey(
        'OperationalPosition', related_name='points_of_work',
        on_delete=models.SET_NULL, blank=True, null=True,
    )
    route_node = models.ForeignKey(
        'RouteNode', related_name='points_of_work',
        on_delete=models.SET_NULL, blank=True, null=True,
    )
    max_throughput = models.IntegerField(blank=True, null=True, help_text='Maximum throughput in containers per hour')
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ('location', 'code')

    def __str__(self):
        return f"{self.code} - {self.name}"


###################################
class ProcessHierarchy(models.Model):
    GATE = 'gate'
    RAIL = 'rail'
    YARD = 'yard'
    QUAY = 'quay'
    OTHER = 'other'

    PROCESS_AREA_CHOICES = [
        (GATE, 'Gate'),
        (RAIL, 'Rail'),
        (YARD, 'Yard'),
        (QUAY, 'Quay'),
        (OTHER, 'Other'),
    ]

    name = models.CharField(max_length=100)
    code = models.CharField(max_length=30, unique=True)
    process_area = models.CharField(
        max_length=10,
        choices=PROCESS_AREA_CHOICES,
        default=OTHER,
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        related_name='children',
        null=True,
        blank=True,
    )
    description = models.TextField(blank=True, default='')
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['process_area', 'display_order', 'name']
        verbose_name_plural = 'process hierarchies'

    def __str__(self):
        return f"{self.get_process_area_display()} / {self.name}"


###################################
class BusinessProcess(models.Model):
    TRIGGER_MANUAL = 'manual'
    TRIGGER_REST_API = 'rest_api'
    TRIGGER_MQTT = 'mqtt'
    TRIGGER_TIMER = 'timer'
    TRIGGER_EVENT = 'event'

    TRIGGER_CHOICES = [
        (TRIGGER_MANUAL, 'Manual'),
        (TRIGGER_REST_API, 'REST API'),
        (TRIGGER_MQTT, 'MQTT'),
        (TRIGGER_TIMER, 'Timer'),
        (TRIGGER_EVENT, 'Application Event'),
    ]

    name = models.CharField(max_length=120)
    code = models.CharField(max_length=40, unique=True)
    hierarchy = models.ForeignKey(
        'ProcessHierarchy',
        on_delete=models.SET_NULL,
        related_name='business_processes',
        null=True,
        blank=True,
    )
    location = models.ForeignKey(
        'Location',
        on_delete=models.CASCADE,
        related_name='business_processes',
        null=True,
        blank=True,
        help_text='Optional terminal-specific process specialization.',
    )
    description = models.TextField(blank=True, default='')
    owner_role = models.CharField(max_length=80, blank=True, default='')
    trigger_type = models.CharField(
        max_length=12,
        choices=TRIGGER_CHOICES,
        default=TRIGGER_MANUAL,
    )
    input_schema = models.JSONField(default=dict, blank=True)
    output_schema = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['hierarchy__process_area', 'hierarchy__display_order', 'name']

    def __str__(self):
        return self.name


###################################
class ProcessVersion(models.Model):
    DRAFT = 'draft'
    PUBLISHED = 'published'
    RETIRED = 'retired'

    STATUS_CHOICES = [
        (DRAFT, 'Draft'),
        (PUBLISHED, 'Published'),
        (RETIRED, 'Retired'),
    ]

    business_process = models.ForeignKey(
        'BusinessProcess',
        on_delete=models.CASCADE,
        related_name='versions',
    )
    version_number = models.PositiveIntegerField()
    version_label = models.CharField(max_length=50, blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    is_current = models.BooleanField(default=False)
    nodered_flow = models.JSONField(
        default=list,
        blank=True,
        help_text='Node-RED flow JSON, typically a list of tab/node/config objects.',
    )
    input_signals = models.JSONField(
        default=list,
        blank=True,
        help_text='Signals this process listens to, such as REST endpoints, MQTT topics, or application events.',
    )
    output_signals = models.JSONField(
        default=list,
        blank=True,
        help_text='Signals this process emits, such as MQTT topics, REST calls, or SOP updates.',
    )
    sop_markdown = models.TextField(blank=True, default='')
    mqtt_announcement_topic = models.CharField(
        max_length=200,
        blank=True,
        default='',
        help_text='Future MQTT topic for broadcasting process-version changes.',
    )
    created_by = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        related_name='process_versions',
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['business_process__name', '-version_number']
        unique_together = ('business_process', 'version_number')

    def clean(self):
        if self.nodered_flow is not None and not isinstance(self.nodered_flow, (list, dict)):
            raise ValidationError('Node-RED flow must be a JSON array or object.')
        for field_name in ['input_signals', 'output_signals']:
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, list):
                raise ValidationError({field_name: 'Signal definitions must be a JSON array.'})

    def save(self, *args, **kwargs):
        if self.is_current:
            ProcessVersion.objects.filter(
                business_process=self.business_process,
                is_current=True,
            ).exclude(pk=self.pk).update(is_current=False)
        if self.status == self.PUBLISHED and self.published_at is None:
            from django.utils import timezone
            self.published_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self):
        label = self.version_label or f"v{self.version_number}"
        return f"{self.business_process.code} {label}"


###################################
class BusinessProcessExecution(models.Model):
    QUEUED = 'queued'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CANCELLED = 'cancelled'

    STATUS_CHOICES = [
        (QUEUED, 'Queued'),
        (RUNNING, 'Running'),
        (COMPLETED, 'Completed'),
        (FAILED, 'Failed'),
        (CANCELLED, 'Cancelled'),
    ]

    business_process = models.ForeignKey(
        'BusinessProcess',
        on_delete=models.CASCADE,
        related_name='executions',
    )
    process_version = models.ForeignKey(
        'ProcessVersion',
        on_delete=models.PROTECT,
        related_name='executions',
    )
    location = models.ForeignKey(
        'Location',
        on_delete=models.SET_NULL,
        related_name='business_process_executions',
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=QUEUED)
    trigger_source = models.CharField(max_length=120, blank=True, default='')
    trigger_payload = models.JSONField(default=dict, blank=True)
    result_payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default='')
    requested_by = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        related_name='business_process_executions',
        null=True,
        blank=True,
    )
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-started_at']

    def clean(self):
        if self.process_version_id and self.business_process_id:
            if self.process_version.business_process_id != self.business_process_id:
                raise ValidationError('Process version must belong to the selected business process.')

    def mark_completed(self, result_payload=None):
        from django.utils import timezone
        self.status = self.COMPLETED
        self.result_payload = result_payload or {}
        self.completed_at = timezone.now()
        self.save(update_fields=['status', 'result_payload', 'completed_at'])

    def __str__(self):
        return f"{self.business_process.code} execution #{self.pk}"
