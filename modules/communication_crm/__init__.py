"""Isolated communication CRM module."""

from .models import create_crm_tables
from .lead_monitor_router import router as lead_monitor_router
from .router import router as crm_router

__all__ = ["create_crm_tables", "crm_router", "lead_monitor_router"]
