"""
Django signals for the workflows building block.

Signals are fired by services.py after successful DB writes.
Consumers (audit, notifications) connect in handlers.py and
apps.py ready().

Signal naming convention: work_item_<past_tense_verb>
"""

from django.dispatch import Signal

# Fired when a new WorkItem is created
work_item_created = Signal()  # providing: work_item, actor

# Fired when assigned_to changes
work_item_assigned = Signal()  # providing: work_item, assignee, actor

# Fired on every status transition
work_item_status_changed = Signal()  # providing: work_item, old_status, new_status, actor, notes

# Fired when escalation_level is incremented
work_item_escalated = Signal()  # providing: work_item, level, actor, reason

# Fired when a staff comment is added
work_item_commented = Signal()  # providing: work_item, comment, actor
