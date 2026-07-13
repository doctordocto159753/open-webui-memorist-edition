ALTER TABLE model_role_defaults
DROP CONSTRAINT IF EXISTS model_role_defaults_role_key;

CREATE UNIQUE INDEX IF NOT EXISTS uq_model_role_defaults_scoped
ON model_role_defaults (
    role,
    COALESCE(workspace_uuid, ''),
    COALESCE(project_uuid, '')
);
