from django.contrib.auth.models import User, Group
from rest_framework import serializers, generics
from . models import Location, Block, Pile, Position, ContainerUnit, ContainerVisit, ISOType, ContainerOwner, Che, WorkQueue, Order, OrderLeg, CarrierVisit


####  Position for container view
class PositionSerializerShort(serializers.ModelSerializer):

    class Meta:
        model = Position
        fields = ['pk', '__str__','pile', 'is_reserved','is_occupied','tier']
        
        
class ContainerVisitSerializerShort(serializers.ModelSerializer):
    
    class Meta:
        model = ContainerVisit
        fields = ['pk', 'category', 'container_number', 'ib_carrier', 'ob_carrier', 'location', 'eta', 'etd', 'weight', 'pod', 'pod2', 'requires_power', 'is_imdg', 'x_position', 'y_position', 'z_position']
                
        
####  Position
class PositionSerializer(serializers.ModelSerializer):
    container = ContainerVisitSerializerShort(many=True, read_only=True)
    
    def get_formatted_str(self, obj):
        block_name = obj.pile.block.name
        bay = f"{obj.pile.bay:02d}"
        row = f"{obj.pile.row:02d}"
        tier = f"{obj.tier:02d}"
        return f"{block_name}.{bay}.{row}.{tier}"

    __str__ = serializers.SerializerMethodField(method_name='get_formatted_str')

    class Meta:
        model = Position
        fields = ['pk', '__str__', 'container_visit_id', 'is_reserved', 'is_occupied', 'tier', 'container']
       
    def partial_update(self, instance, validated_data):
        
        position = Position.objects.get(pk=instance.id)
        Position.objects.filter(pk=instance.id)\
                           .update(**validated_data)
        if position.container is Null:
            position.is_occupied = True
        else:
            position.is_occupied = False
        return position
        
####  Pile
class PileSerializer(serializers.ModelSerializer):

    
    def __init__(self, *args, **kwargs):
        kwargs['partial'] = True
        super(PileSerializer, self).__init__(*args, **kwargs)

    positions = PositionSerializer(many=True, read_only=True)
    
    def get_formatted_str(self, obj):
        block_name = obj.block.name
        bay = f"{obj.bay:02d}"
        row = f"{obj.row:02d}"
        return f"{block_name}.{bay}.{row}"

    def get_bay_name(self, obj):
        return f"{obj.bay:02d}"

    def get_row_name(self, obj):
        return f"{obj.row:02d}"

    __str__ = serializers.SerializerMethodField(method_name='get_formatted_str')
    bay_name = serializers.SerializerMethodField(method_name='get_bay_name')
    row_name = serializers.SerializerMethodField(method_name='get_row_name')
    block_name = serializers.CharField(source='block.name', read_only=True)

    class Meta:
        model = Pile
        fields = [
            'pk', 'block', 'block_name', 'bay', 'bay_name', 'row', 'row_name', 'tiers',
            'allows_in', 'allows_out', 'is_restricted', 'allows_20', 'allows_40', 'allows_45',
            'x_position', 'y_position', '__str__', 'positions'
        ]
        
    def partial_update(self, instance, validated_data):
        
        pile = Pile.objects.get(pk=instance.id)
        Pile.objects.filter(pk=instance.id)\
                           .update(**validated_data)
        return pile
        
####  Block
class BlockSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True)
    block_type_display = serializers.CharField(source='get_block_type_display', read_only=True)
    piles = PileSerializer(many=True, read_only=True)
    
    # Statistics fields
    total_piles = serializers.IntegerField(read_only=True)
    total_positions_20 = serializers.IntegerField(read_only=True)
    total_positions_40 = serializers.IntegerField(read_only=True)
    total_positions_45 = serializers.IntegerField(read_only=True)
    free_positions_20 = serializers.IntegerField(read_only=True)
    free_positions_40 = serializers.IntegerField(read_only=True)
    free_positions_45 = serializers.IntegerField(read_only=True)

    class Meta:
        model = Block
        fields = ['pk', 'name', 'location', 'location_name', 'block_type', 'block_type_display',
                 'max_bays', 'max_rows', 'max_tier', 'x_position', 'y_position', 'is_heapzone',
                 'total_piles', 'total_positions_20', 'total_positions_40', 'total_positions_45',
                 'free_positions_20', 'free_positions_40', 'free_positions_45', 'piles']
        
####  Che
class CheSerializer(serializers.ModelSerializer):
    equipment_type_name = serializers.CharField(source='equipment_type.name', read_only=True)
    equipment_type_code = serializers.CharField(source='equipment_type.equipment_type', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True)
    current_block_name = serializers.CharField(source='current_block.name', read_only=True, allow_null=True)
    current_user_name = serializers.CharField(source='current_user.username', read_only=True, allow_null=True)

    def __init__(self, *args, **kwargs):
        kwargs['partial'] = True
        super(CheSerializer, self).__init__(*args, **kwargs)

    cblock = BlockSerializer(many=False, read_only=True)
    class Meta:
        model = Che
        fields = [
            'pk', 'status', 'name', 'equipment_type', 'equipment_type_name', 'equipment_type_code',
            'current_block', 'current_block_name', 'current_user', 'current_user_name',
            'location', 'location_name', 'twistlock_closed', 'x_position', 'y_position', 
            'fuel_level', 'last_maintenance', 'cblock'
        ]
        
    def partial_update(self, instance, validated_data):
        
        che = Che.objects.get(pk=instance.id)
        Che.objects.filter(pk=instance.id)\
                           .update(**validated_data)
        return che

####  Location
class LocationSerializer(serializers.ModelSerializer):
    
    blocks = BlockSerializer(many=True, read_only=True)
    ches = CheSerializer(many=True, read_only=True)
    class Meta:
        model = Location
        fields = ['pk','name','location_type','blocks', 'ches']
        


class UserSerializer(serializers.HyperlinkedModelSerializer):
    
    class Meta:
        model = User
        fields = ['url', 'username', 'email', 'groups']

class GroupSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Group
        fields = ['url', 'name']
        
class ContainerUnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContainerUnit
        fields = ['iso_type', 'owner', 'container_number', 'is_active', 'is_in_terminal']

class ContainerVisitSerializer(serializers.ModelSerializer):
    position = PositionSerializerShort(many=False, read_only=True)
    container_number_name = serializers.CharField(source='container_number.container_number', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True)
    pod_name = serializers.CharField(source='pod.name', read_only=True, allow_null=True)
    pod2_name = serializers.CharField(source='pod2.name', read_only=True, allow_null=True)
    ib_carrier_name = serializers.CharField(source='ib_carrier.name', read_only=True, allow_null=True)
    ob_carrier_name = serializers.CharField(source='ob_carrier.name', read_only=True, allow_null=True)
    category_display = serializers.CharField(source='get_category_display', read_only=True)

    class Meta:
        model = ContainerVisit
        fields = [
            'pk', 'category', 'category_display', 'container_number', 'container_number_name',
            'ib_carrier', 'ib_carrier_name', 'ob_carrier', 'ob_carrier_name',
            'location', 'location_name', 'position', 'eta', 'etd', 'weight',
            'pod', 'pod_name', 'pod2', 'pod2_name', 'requires_power', 'is_imdg',
            'x_position', 'y_position', 'z_position', 'bill_of_lading', 'booking'
        ]

class OrderLegSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderLeg
        fields = ['order', 'che','start_position','end_position']

class OrderSerializer(serializers.ModelSerializer):
    order_legs = OrderLegSerializer(many=True, read_only=True)
    class Meta:
        model = Order
        fields = ['order_type', 'container_number', 'container_number', 'work_queue', 'etc', 'atc', 'from_position', 'to_position', 'order_legs']
        
class WorkQueueSerializer(serializers.ModelSerializer):
    orders = OrderSerializer(many=True, read_only=True)
    
    class Meta:
        model = WorkQueue
        fields = ['name', 'is_active', 'description', 'current_user','orders']

class UserSerializer(serializers.ModelSerializer):
    work_queue = serializers.PrimaryKeyRelatedField(many=False, queryset=WorkQueue.objects.all())

    class Meta:
        model = User
        fields = ['id', 'username', 'work_queue']


#### CarrierVisit Serializers

class CarrierVisitSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    visit_type_display = serializers.CharField(source='get_visit_type_display', read_only=True)
    container_count = serializers.SerializerMethodField()

    def get_container_count(self, obj):
        # Count containers where this visit is ib_carrier or ob_carrier
        return ContainerVisit.objects.filter(
            models.Q(ib_carrier=obj) | models.Q(ob_carrier=obj)
        ).count()

    class Meta:
        model = CarrierVisit
        fields = [
            'id', 'name', 'visit_type', 'visit_type_display', 'status', 'status_display',
            'location', 'location_name', 'eta', 'etd', 'ata', 'atd', 'vessel_name',
            'voyage_number', 'carrier', 'berth', 'container_count',
            'created_at', 'last_updated'
        ]
        read_only_fields = ['created_at', 'last_updated', 'container_count']


#### Bulk Container Creation Serializer
class BulkContainerVisitSerializer(serializers.Serializer):
    containers = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=False
    )

    def create(self, validated_data):
        containers_data = validated_data['containers']
        created_visits = []

        for container_data in containers_data:
            # Extract fields
            container_number = container_data.get('container_number')
            iso_type_code = container_data.get('iso_type', '2200')  # Default
            owner_code = container_data.get('owner', 'DEFAULT')  # Default
            category = container_data.get('category', 'I')
            location_id = container_data.get('location')
            weight = container_data.get('weight', 0)
            bill_of_lading = container_data.get('bill_of_lading')
            booking = container_data.get('booking')
            ib_carrier_id = container_data.get('ib_carrier')
            ob_carrier_id = container_data.get('ob_carrier')
            pod_id = container_data.get('pod')
            eta = container_data.get('eta')
            etd = container_data.get('etd')

            # Get or create ContainerUnit
            iso_type, _ = ISOType.objects.get_or_create(
                iso_type=iso_type_code,
                defaults={'length': iso_type_code[0], 'height': 'NC'}
            )
            owner, _ = ContainerOwner.objects.get_or_create(
                code=owner_code,
                defaults={'name': owner_code}
            )
            container_unit, _ = ContainerUnit.objects.get_or_create(
                container_number=container_number,
                defaults={'iso_type': iso_type, 'owner': owner}
            )

            # Create ContainerVisit
            container_visit = ContainerVisit.objects.create(
                container_number=container_unit,
                category=category,
                location_id=location_id,
                weight=weight,
                bill_of_lading=bill_of_lading,
                booking=booking,
                ib_carrier_id=ib_carrier_id,
                ob_carrier_id=ob_carrier_id,
                pod_id=pod_id,
                eta=eta,
                etd=etd,
                status=ContainerVisit.PREADVISED
            )
            created_visits.append(container_visit)

        return created_visits