
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ContainerOwner, ContainerUnit, ISOType, Che, WorkQueue, Order, OrderLeg, Block, Pile, Position


@receiver(post_save, sender=Block)
def create_block(sender, instance, created, **kwargs):
    if created:
        #Block.objects.create(instance)
        max_rows = instance.max_rows
        max_bays = instance.max_bays
        max_tier = instance.max_tier
        x_start_pos = instance.x_position
        y_start_pos = instance.y_position
        x_offset = 10
        y_offset = 10
        
        for bay in range(1, max_bays+1):
            for row in range(1,max_rows+1):
                pile = Pile.objects.create(block=instance, bay=bay, row=row, allows_in=True, allows_out=True,is_restricted=False, x_position = x_start_pos + bay*x_offset, y_position = y_start_pos + row*y_offset)
                for tier in range(1, max_tier+1):
                    Position.objects.create(pile=pile, is_reserved=False, is_occupied=False, tier=tier)
   

@receiver(post_save, sender=Block)
def save_block(sender, instance, **kwargs):
    created = Block.objects.create(instance)
    if created:
        block = Block.objects.get(pk=instance.pk)
        block_piles = Pile.objects.filter(block = block.pk)
        block_piles.delete()
    
        max_rows = instance.max_rows
        max_bays = instance.max_bays
        max_tier = instance.max_tier
        x_start_pos = instance.x_position
        y_start_pos = instance.y_position
        x_offset = 10
        y_offset = 10
        
        for bay in range(1, max_bays+1):
            for row in range(1,max_rows+1):
                pile = Pile.objects.create(block=instance, bay=bay, row=row, allows_in=True, allows_out=True,is_restricted=False, x_position = x_start_pos + bay*x_offset, y_position = y_start_pos + row*y_offset)
                for tier in range(1, max_tier+1):
                    Position.objects.create(pile=pile, is_reserved=False, is_occupied=False, tier=tier)
                    
    post_save.disconnect(save_block, sender=Block)
    instance.save()
    post_save.connect(save_block, sender=Block)
    
    