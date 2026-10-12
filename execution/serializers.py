from rest_framework import serializers

from .models import (
    DispatchAssignment,
    EquipmentCommand,
    EquipmentState,
    ExecutionOperation,
    ExecutionOrder,
)


class EquipmentStateSerializer(serializers.ModelSerializer):
    class Meta:
        model = EquipmentState
        fields = "__all__"


class EquipmentCommandSerializer(serializers.ModelSerializer):
    class Meta:
        model = EquipmentCommand
        fields = "__all__"


class DispatchAssignmentSerializer(serializers.ModelSerializer):
    equipment_name = serializers.CharField(source="equipment.name", read_only=True)
    commands = EquipmentCommandSerializer(many=True, read_only=True)

    class Meta:
        model = DispatchAssignment
        fields = "__all__"


class ExecutionOperationSerializer(serializers.ModelSerializer):
    assignment = DispatchAssignmentSerializer(read_only=True)

    class Meta:
        model = ExecutionOperation
        fields = "__all__"


class ExecutionOrderSerializer(serializers.ModelSerializer):
    operations = ExecutionOperationSerializer(many=True, read_only=True)

    class Meta:
        model = ExecutionOrder
        fields = "__all__"


class DispatchRequestSerializer(serializers.Serializer):
    equipment_id = serializers.IntegerField(required=False, min_value=1)


class CommandEventSerializer(serializers.Serializer):
    event = serializers.ChoiceField(choices=[
        "acknowledged", "started", "container_picked_up", "container_dropped",
        "completed", "failed", "cancelled",
    ])
    position_id = serializers.IntegerField(required=False, min_value=1)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=1000)

