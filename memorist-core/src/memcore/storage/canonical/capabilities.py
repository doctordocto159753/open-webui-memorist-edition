from pydantic import BaseModel


class StoreCapabilities(BaseModel):
    concurrent_writers: bool
    row_level_locking: bool
    skip_locked: bool
    json_native: bool
    full_text_search: bool
    advisory_locks: bool
    listen_notify: bool
    vector_extension: bool
