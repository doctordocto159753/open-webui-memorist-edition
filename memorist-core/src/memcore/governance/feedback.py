import sqlite3
from typing import Any

from memcore.governance.privacy import PrivacyService
from memcore.governance.repositories import GovernanceRepository


class FeedbackService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.repository = GovernanceRepository(connection)

    def submit_feedback(
        self,
        feedback_type: str,
        actor_type: str,
        memory_uuid: str | None = None,
        memory_version_uuid: str | None = None,
        attachment_uuid: str | None = None,
        response_message_uuid: str | None = None,
        rating: int | None = None,
        comment: str | None = None,
        actor_uuid: str | None = None,
    ) -> dict[str, Any]:
        feedback = self.repository.create_feedback(
            feedback_type=feedback_type,
            actor_type=actor_type,
            memory_uuid=memory_uuid,
            memory_version_uuid=memory_version_uuid,
            attachment_uuid=attachment_uuid,
            response_message_uuid=response_message_uuid,
            rating=rating,
            comment=comment,
            actor_uuid=actor_uuid,
        )
        if feedback_type == "privacy_concern":
            request = PrivacyService(self.connection).preview_request(
                request_type="forget_memory",
                target_type="memory",
                target_uuid=memory_uuid,
                requested_scope={"memory_uuid": memory_uuid, "source": "feedback"},
                actor_type=actor_type,
                actor_uuid=actor_uuid,
            )
            feedback["privacy_request_uuid"] = request["privacy_request_uuid"]
        elif feedback_type in {"incorrect", "outdated"} and memory_uuid is not None:
            change_request = self.repository.create_change_request(
                requested_operation="mark_outdated"
                if feedback_type == "outdated"
                else "mark_incorrect",
                actor_type=actor_type,
                memory_uuid=memory_uuid,
                reason=comment,
                actor_uuid=actor_uuid,
                impact_preview={"created_from_feedback_uuid": feedback["feedback_uuid"]},
            )
            feedback["change_request_uuid"] = change_request["change_request_uuid"]
        return feedback
