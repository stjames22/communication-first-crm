"""Isolated communication CRM module."""

from .models import create_crm_tables
from .router import router as crm_router

__all__ = ["create_crm_tables", "crm_router"]
