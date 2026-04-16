from django.db import models
from django.core.validators import RegexValidator
from django.db.models.signals import post_save
from django.core.exceptions import ValidationError


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
    x_position = models.FloatField(blank=True, null=True)
    y_position = models.FloatField(blank=True, null=True)
    z_position = models.FloatField(blank=True, null=True)
    bill_of_lading = models.CharField(max_length=20, blank=True, null=True)
    booking = models.CharField(max_length=20, blank=True, null=True)

    # New fields for status tracking
    PREADVISED = 'Preadvised'
    ARRIVED = 'Arrived'
    AT_YARD = 'AtYard'
    AT_QC = 'AtQC'
    ON_VESSEL = 'OnVessel'
    DEPARTED = 'Departed'
    COMPLETED = 'Completed'

    STATUS_CHOICES = [
        (PREADVISED, 'Preadvised'),
        (ARRIVED, 'Arrived'),
        (AT_YARD, 'At Yard'),
        (AT_QC, 'At QC'),
        (ON_VESSEL, 'On Vessel'),
        (DEPARTED, 'Departed'),
        (COMPLETED, 'Completed'),
    ]

    status = models.CharField(
        max_length=15,
        choices=STATUS_CHOICES,
        default=PREADVISED,
    )
    last_updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.container_number.container_number


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

    def __str__(self):
        return "%s" % (self.name)


###################################
class Block(models.Model):
    TZ = 'TZ'
    EP = 'EP'
    AB = 'AB'
    MB = 'MB'

    BLOCK_TYPES = [
        (TZ, 'Transfer Zone'),
        (EP, 'Entry Point'),
        (AB, 'Automated Block'),
        (MB, 'Manual Block'),
    ]

    location = models.ForeignKey(
        'Location', related_name='blocks',
        on_delete=models.CASCADE,
    )

    block_type = models.CharField(
        max_length=2,
        choices=BLOCK_TYPES,
        default=AB,
    )

    name = models.CharField(max_length=30, unique=False)
    max_bays = IntegerRangeField(min_value=1, max_value=100)
    max_rows = IntegerRangeField(min_value=1, max_value=100)
    max_tier = IntegerRangeField(min_value=1, max_value=20)
    x_position = models.FloatField(blank=True, null=True)
    y_position = models.FloatField(blank=True, null=True)
    is_heapzone = models.BooleanField(default=0)
    
    objects = models.Manager() # The default manager.

    class Meta:
        unique_together = ('location', 'name',)

    def __str__(self):
        return "%s-%s" % (self.location, self.name)

    def save(self, **kwargs):
        created = super().save(**kwargs)  # Call the "real" save() method.
        block = Block.objects.get(pk=self.pk)
        block_piles = Pile.objects.filter(block = block.pk)
        block_piles.delete()
        
        max_rows = block.max_rows
        max_bays = block.max_bays
        max_tier = block.max_tier
        x_start_pos = block.x_position if block.x_position is not None else 0
        y_start_pos = block.y_position if block.y_position is not None else 0
        x_offset = 1.5
        y_offset = 0.8
        
        for bay in range(1, max_bays+1):
            for row in range(1,max_rows+1):
                pile = Pile.objects.create(block=block, bay=bay, row=row, allows_in=True, allows_out=True,is_restricted=False, x_position = x_start_pos + bay*x_offset, y_position = y_start_pos + row*y_offset)
                pile.save()
                for tier in range(1, max_tier+1):
                    position = Position.objects.create(pile=pile, is_reserved=False, is_occupied=False, tier=tier)
                    position.save()
        
###################################
class Pile(models.Model):
    block = models.ForeignKey(
        'Block', related_name='piles',
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

    x_position = models.FloatField(blank=True, null=True)
    y_position = models.FloatField(blank=True, null=True)

    class Meta:
        unique_together = ('block', 'bay', 'row')
        # No need for additional constraints as the block's location is already part of the uniqueness
        # through the block foreign key

    def __str__(self):
        return "%s-%s-%s-%s-%s" % (self.block.location.name, self.block.name, self.block, self.bay, self.row)

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
        return "%s-%s-%s-%s" % (self.pile.block, self.pile.bay, self.pile.row, self.tier)

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

    order_type = models.CharField(
        max_length=2,
        choices=ORDER_TYPES,
        default=HOUSEKEEPING,
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
    from_position = models.ForeignKey(
        'Position',
        on_delete=models.RESTRICT,
        related_name='from_position', blank=True, null=True
    )
    to_position = models.ForeignKey(
        'Position',
        on_delete=models.RESTRICT,
        related_name='to_position', blank=True, null=True
    )

    def __str__(self):
        return "%s-%s-%s" % (self.work_queue.name, self.pk, self.container_number,)


###################################
class OrderLeg(models.Model):
    order = models.ForeignKey(
        'Order', related_name='order_legs',
        on_delete=models.CASCADE, blank=True, null=True,
    )
    che = models.ForeignKey(
        'Che',
        on_delete=models.RESTRICT, blank=True, null=True,
    )
    distance = IntegerRangeField(min_value=1, max_value=10000, blank=True, null=True)
    start_position = models.ForeignKey(
        'Pile',
        on_delete=models.RESTRICT,
        related_name='start_pile', blank=True, null=True
    )
    end_position = models.ForeignKey(
        'Pile',
        on_delete=models.RESTRICT,
        related_name='end_pile', blank=True, null=True
    )

    def __str__(self):
        return "%s-%s-%s" % (self.order, self.id, self.order.container_number)


###################################
class Che(models.Model):
    IL = 'IL'
    WK = 'WK'
    OF = 'OF'
    BD = 'BD'
    MA = 'MA'

    STATUS_TYPES = [
        (IL, 'Idle'),
        (WK, 'Working'),
        (OF, 'Offline'),
        (BD, 'Breakdown'),
        (MA, 'In Maintenance'),
    ]

    status = models.CharField(
        max_length=2,
        choices=STATUS_TYPES,
        default=OF,
    )
    is_available = models.BooleanField(default=False)
    name = models.CharField(max_length=30, unique=True)
    equipment_type = models.ForeignKey(
        'EquipmentType',
        on_delete=models.CASCADE,
    )
    current_block = models.ForeignKey(
        'Block',
        on_delete=models.RESTRICT, blank=True, null=True
    )
    current_user = models.ForeignKey(
        'auth.User',
        related_name='equipments',
        on_delete=models.RESTRICT, blank=True, null=True
    )
    # highlighted = models.TextField()

    x_position = models.FloatField(blank=True, null=True)
    y_position = models.FloatField(blank=True, null=True)
    # position
    twistlock_closed = models.BooleanField(default=False)

    location = models.ForeignKey(
        'Location',
        on_delete=models.CASCADE,
    )

    fuel_level = models.IntegerField(default=100)
    last_maintenance = models.DateTimeField(blank=True, null=True)
    # assigned_tasks: This would be a ManyToMany or ForeignKey to a Task/Order model

    def __str__(self):
        return self.name


###################################
class EquipmentType(models.Model):
    name = models.CharField(max_length=30, unique=True)
    running_hours = models.IntegerField(default=0)

    TT = 'TT'
    SC = 'SC'
    RTG = 'RTG'
    ASC = 'ASC'
    STS = 'STS'

    EQUIPMENT_TYPES = [
        (TT, 'Terminal Tractor'),
        (SC, 'Shuttle Carrier'),
        (RTG, 'RT Gantry'),
        (ASC, 'Auto Stacking Crane'),
        (STS, 'Ship to Shore'),
    ]

    equipment_type = models.CharField(
        max_length=3,
        choices=EQUIPMENT_TYPES,
        default=SC,
    )

    def __str__(self):
        return self.name


###################################
class EquipmentPart(models.Model):
    name = models.CharField(max_length=30, unique=True)
    running_hours = models.IntegerField(default=0)
    equipment_type = models.ForeignKey(
        'EquipmentType',
        on_delete=models.RESTRICT,
    )

    def __str__(self):
        return self.name



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

    duration = IntegerRangeField(min_value=1, max_value=1440, blank=True, null=True, help_text='Duration in minutes')

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

    periodicity = models.CharField(max_length=20, choices=[
        ('Daily', 'Daily'),
        ('Weekly', 'Weekly'),
        ('Bi-weekly', 'Bi-weekly'),
        ('Monthly', 'Monthly'),
    ], default='Weekly')

    def __str__(self):
        return self.name


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

    def __str__(self):
        return f"{self.name} ({self.status})"


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
        visit_name = self.inbound_carrier_visit.name
        service_name = self.outbound_carrier_service.name
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
    x_position = models.FloatField(blank=True, null=True)
    y_position = models.FloatField(blank=True, null=True)
    max_throughput = models.IntegerField(blank=True, null=True, help_text='Maximum throughput in containers per hour')
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ('location', 'code')

    def __str__(self):
        return f"{self.code} - {self.name}"


###################################
class POWConnection(models.Model):
    from_pow = models.ForeignKey(
        'PointOfWork',
        on_delete=models.CASCADE,
        related_name='outgoing_connections'
    )
    to_pow = models.ForeignKey(
        'PointOfWork',
        on_delete=models.CASCADE,
        related_name='incoming_connections'
    )
    distance = models.IntegerField(help_text='Distance in meters')
    max_throughput = models.IntegerField(help_text='Maximum throughput in containers per hour')
    is_bidirectional = models.BooleanField(default=False)
    cost = models.FloatField(default=1.0, help_text='Cost factor for pathfinding algorithms')
    is_enabled = models.BooleanField(default=True)

    class Meta:
        unique_together = ('from_pow', 'to_pow')

    def __str__(self):
        direction = '↔' if self.is_bidirectional else '→'
        return f"{self.from_pow.code} {direction} {self.to_pow.code} ({self.distance}m)"