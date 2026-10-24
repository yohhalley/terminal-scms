from rest_framework import serializers

from .models import (
    DispatchAssignment,
    EnterpriseProjection,
    EquipmentCommand,
    EquipmentState,
    ExecutionOperation,
    ExecutionOrder,
    SimulationSession,
    SiteProjection,
)


class EnterpriseProjectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnterpriseProjection
        fields = "__all__"


class SiteProjectionSerializer(serializers.ModelSerializer):
    enterprise_code = serializers.CharField(source="enterprise.code", read_only=True)
    is_ready = serializers.BooleanField(read_only=True)

    class Meta:
        model = SiteProjection
        fields = "__all__"


class SimulationSessionSerializer(serializers.ModelSerializer):
    site_uuid = serializers.UUIDField(source="site.source_id", read_only=True)
    site_code = serializers.CharField(source="site.code", read_only=True)
    enterprise_code = serializers.CharField(source="site.enterprise.code", read_only=True)
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = SimulationSession
        exclude = ["token_hash", "bootstrap_payload"]


class StartSimulationSerializer(serializers.Serializer):
    simulator_version = serializers.CharField(required=False, default="local", max_length=80)
    time_scale = serializers.FloatField(required=False, default=1.0, min_value=0.01, max_value=100.0)


class RunnerClaimSerializer(serializers.Serializer):
    runner_identity = serializers.CharField(max_length=160)


class RunnerStatusSerializer(serializers.Serializer):
    runner_identity = serializers.CharField(max_length=160)
    status = serializers.ChoiceField(choices=[
        SimulationSession.RUNNER_STARTING,
        SimulationSession.RUNNER_RUNNING,
        SimulationSession.RUNNER_STOPPED,
        SimulationSession.RUNNER_FAILED,
    ])
    failure_reason = serializers.CharField(required=False, allow_blank=True, max_length=2000)


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
