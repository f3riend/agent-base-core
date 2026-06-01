from workflow_service import cancel_workflows, create_workflow


def execute_rule_actions(
    rule,
    subject_type,
    subject_id,
    event_id: int = 0,
    user_id: int = 1,
):
    workflow = rule.get("workflow")
    cancel_workflow = rule.get("cancel_workflow")
    delay = rule.get("delay", 0)

    entity_type = subject_type.lower()

    if workflow:
        workflow_id = create_workflow(
            workflow_name=workflow,
            entity_type=entity_type,
            entity_id=subject_id,
            delay_days=delay,
            event_id=event_id,
            user_id=user_id,
        )
        if workflow_id:
            print(
                f"[ACTION] Workflow scheduled: {workflow} "
                f"(#{workflow_id})"
            )
        else:
            print(
                f"[ACTION] Workflow already exists: {workflow}"
            )

    if cancel_workflow:
        cancel_workflows(
            entity_type=entity_type,
            entity_id=subject_id,
            reason="rule_cancelled",
            workflow_name=cancel_workflow,
            event_id=event_id,
            user_id=user_id,
        )
        print(f"[ACTION] Workflow cancelled: {cancel_workflow}")
