"""Transactional Kafka outbox owned by UserService."""

from user_service.outbox.repository import OutboxEvent, OutboxRepository

__all__ = ("OutboxEvent", "OutboxRepository")