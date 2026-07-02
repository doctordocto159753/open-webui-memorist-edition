from memcore.models import ChangeOperation, ConsolidationOperation


def change_operation_for(operation: ConsolidationOperation) -> ChangeOperation:
    mapping = {
        ConsolidationOperation.ADD: ChangeOperation.ADD,
        ConsolidationOperation.UPDATE: ChangeOperation.UPDATE,
        ConsolidationOperation.SUPERSEDE: ChangeOperation.SUPERSEDE,
        ConsolidationOperation.CONTRADICT: ChangeOperation.CONTRADICT,
        ConsolidationOperation.RETRACT: ChangeOperation.RETRACT,
        ConsolidationOperation.FORGET: ChangeOperation.FORGET,
        ConsolidationOperation.REINFORCE: ChangeOperation.REINFORCE,
    }
    return mapping.get(operation, ChangeOperation.ADD)
