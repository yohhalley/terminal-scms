from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import EquipmentState


class EquipmentStateOverrideForm(forms.ModelForm):
    class Meta:
        model = EquipmentState
        fields = [
            "powered_on",
            "working",
            "is_available",
            "is_dispatched",
            "is_in_maintenance",
            "latitude",
            "longitude",
            "rotation",
            "fuel_level",
            "energy_level",
            "speed_mps",
        ]
        widgets = {
            "latitude": forms.NumberInput(attrs={"step": "0.0000001"}),
            "longitude": forms.NumberInput(attrs={"step": "0.0000001"}),
            "rotation": forms.NumberInput(attrs={"step": "0.1"}),
            "fuel_level": forms.NumberInput(attrs={"step": "0.1", "min": "0", "max": "100"}),
            "energy_level": forms.NumberInput(attrs={"step": "0.1", "min": "0", "max": "100"}),
            "speed_mps": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }

    def clean(self):
        cleaned = super().clean()
        powered_on = cleaned.get("powered_on", False)
        working = cleaned.get("working", False)
        available = cleaned.get("is_available", False)
        maintenance = cleaned.get("is_in_maintenance", False)
        latitude = cleaned.get("latitude")
        longitude = cleaned.get("longitude")

        if working and not powered_on:
            raise ValidationError("Working equipment must be powered on.")
        if available and not powered_on:
            raise ValidationError("Available equipment must be powered on.")
        if maintenance and (working or available):
            raise ValidationError("Equipment in maintenance cannot be working or available.")
        if (latitude is None) != (longitude is None):
            raise ValidationError("Latitude and longitude must be supplied together.")

        for field in ("fuel_level", "energy_level"):
            value = cleaned.get(field)
            if value is not None and not 0 <= value <= 100:
                self.add_error(field, "Enter a percentage between 0 and 100.")
        return cleaned

    def save_override(self, user):
        equipment = super().save(commit=False)
        now = timezone.now()
        equipment.state_source = EquipmentState.MANUAL
        equipment.manual_override_at = now
        equipment.manual_override_by = user.get_username()
        equipment.source_timestamp = now
        equipment.last_seen_at = now
        raw_state = dict(equipment.raw_state or {})
        raw_state["manual_override"] = {
            "at": now.isoformat(),
            "by": user.get_username(),
        }
        equipment.raw_state = raw_state
        equipment.save()
        return equipment
