class DocStage:
    DRAFT                       = "draft"
    IN_REVIEW                   = "in_review"
    AWAITING_DISPATCH_TO_TARGET = "awaiting_dispatch_to_target"
    AT_TARGET                   = "at_target"
    AWAITING_DISPATCH_TO_ORIGIN = "awaiting_dispatch_to_origin"
    CLOSED                      = "closed"


class CorrespondenceVisibility:
    VISIBLE    = "visible"     # sequence 0, always visible
    INTERNAL   = "internal"    # reply, target directorate only
    DISPATCHED = "dispatched"  # reply, now visible to origin


class CorrespondenceReview:
    DRAFT      = "draft"
    IN_REVIEW  = "in_review"
    SIGNED     = "signed"
    COMPLETED  = "completed"
    DISPATCHED = "dispatched"