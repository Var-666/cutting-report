import type { DatabaseConfiguration as DatabaseData, SavedConnection, SavedConnectionRequestWritable as SavedConnectionRequest, WeldingConnection as WeldingData, WeldingConnectionRequestWritable as WeldingConnectionRequest } from '../api/generated/types.gen'

export type { DatabaseStatus, ConfigurationDiagnostics, DiagnosticProcess, ConnectionTestResult, DatabasePreviewRequest, ConfigurationSaved as SaveResult } from '../api/generated/types.gen'
export type DatabaseSource = 'production' | 'worker' | 'file'
// Editable drafts combine the response with a write-only password and optional presence flag.
export type SqlConnection = Omit<SavedConnection, 'password_configured'> & Pick<SavedConnectionRequest, 'password'> & Partial<Pick<SavedConnection, 'password_configured'>>
export type WeldingConnection = Omit<WeldingData, 'password_configured' | 'enabled'> & Pick<WeldingConnectionRequest, 'password' | 'enabled'> & Partial<Pick<WeldingData, 'password_configured'>>
export type DatabaseConfiguration = Omit<DatabaseData, DatabaseSource | 'welding'> & Record<DatabaseSource, SqlConnection> & { welding: WeldingConnection | null }
export type DiagnosticStatus = import('../api/generated/types.gen').DiagnosticProcess['status']
