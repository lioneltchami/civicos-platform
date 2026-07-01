"""
Volunteer Management BB — Signal receivers.

Receivers are registered in VolunteersConfig.ready() in apps.py.
All receivers use send_robust() at dispatch time (in signals.py callers)
so a failing receiver never rolls back the originating transaction.

Wave 1: Stub receivers only — connection logic wired to Notifications BB
will be implemented in Wave 2 when views/services are built.
"""
# Receivers will be implemented in Wave 2 alongside the services layer.
# This module exists in Wave 1 so that VolunteersConfig.ready() can import it
# without ImportError.
