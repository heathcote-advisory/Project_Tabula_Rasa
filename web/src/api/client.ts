import createClient from 'openapi-fetch';
import type { components, paths } from './schema';

export type Session = components['schemas']['SessionInfo'];
export type SessionState = Session | components['schemas']['SetupSessionInfo'];
export type Account = components['schemas']['Account'];
export type AccountCreate = components['schemas']['AccountCreate'];
export type ErrorCode = components['schemas']['ErrorCode'];
type Envelope = components['schemas']['ErrorEnvelope'];
export type Schemas = components['schemas'];

export class ApiError extends Error {
  constructor(
    public readonly code: ErrorCode,
    public readonly requestId?: string,
    public readonly fields: components['schemas']['FieldError'][] = [],
  ) { super(code); }
}

export function unwrap<T>(result: { data?: T; error?: Envelope; response: Response }): T {
  if (result.error) {
    const error = result.error;
    if (['unauthorized', 'forbidden', 'unavailable'].includes(error.error.code))
      window.dispatchEvent(new Event('tabula:identity-check'));
    throw new ApiError(error.error.code, error.request_id, error.error.fields ?? []);
  }
  if (!result.response.ok || result.data === undefined) {
    throw new ApiError('unexpected', result.response.headers.get('X-Request-ID') ?? undefined);
  }
  return result.data;
}

function confirmEmpty(result: { error?: Envelope; response: Response }) {
  // Preserve the same identity-loss notification for successful-empty commands.
  if (result.error || !result.response.ok) unwrap(result);
}

export function createApi(fetcher: typeof fetch = (...args) => fetch(...args)) {
  // Relative same-origin URLs only. Credentials/identity never enter localStorage.
  const client = createClient<paths>({ fetch: fetcher, credentials: 'same-origin' });
  const headers = (csrf?: string) => ({
    'X-Correlation-ID': crypto.randomUUID(),
    ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
  });
  const safe = async <T>(call: () => Promise<T>): Promise<T> => {
    try { return await call(); }
    catch (error) {
      if (error instanceof ApiError || (error instanceof DOMException && error.name === 'AbortError')) throw error;
      window.dispatchEvent(new Event('tabula:identity-check'));
      throw new ApiError('unavailable');
    }
  };
  const path = (session: Session, record_id: string) => ({ entity_id: session.entity.id, record_id });
  const listParams = (session: Session, signal?: AbortSignal) => ({ params: { path: { entity_id: session.entity.id } }, signal, headers: headers() });
  const mutationParams = (session: Session, id: string) => ({ params: { path: path(session, id) }, headers: headers(session.csrf_token) });
  const createParams = (session: Session) => ({ ...listParams(session), headers: headers(session.csrf_token) });
  return {
    lifecycleStatus: (businessId?: string, signal?: AbortSignal) => safe(async () => unwrap(await client.GET(
      '/api/v1/customer-lifecycle', { params: { query: { business_id: businessId } }, signal, headers: headers() }))),
    requestLifecycle: (s: SessionState, body: Schemas['LifecycleRequest']) => safe(async () => unwrap(await client.POST(
      '/api/v1/customer-lifecycle', { body, headers: headers(s.csrf_token) }))),
    changeLifecycle: (s: SessionState, request_id: string, body: Schemas['LifecycleChange']) => safe(async () => unwrap(await client.POST(
      '/api/v1/customer-lifecycle/{request_id}', { params: { path: { request_id } }, body, headers: headers(s.csrf_token) }))),
    connectionStatus: (s: Session, signal?: AbortSignal) => safe(async () => unwrap(await client.GET(
      '/api/v1/entities/{entity_id}/connections/google-drive', listParams(s, signal)))),
    authorizeDrive: (s: Session, body: Schemas['ConnectionStart']) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/connections/google-drive/authorize', { ...createParams(s), body }))),
    setupDrive: (s: Session, body: Schemas['DriveSetupCommand'], signal?: AbortSignal) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/connections/google-drive/setup', { ...createParams(s), body, signal }))),
    changeConnection: (s: Session, body: Schemas['ConnectionChange']) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/connections/google-drive', { ...createParams(s), body }))),
    session: (signal?: AbortSignal) => safe(async () => unwrap(await client.GET('/api/v1/session', { signal, headers: headers() }))),
    logout: (s?: Pick<SessionState, 'csrf_token'>) => safe(async () => {
      const context = s ?? unwrap(await client.GET('/api/v1/auth/logout-context', {
        headers: { ...headers(), 'X-Tabula-Logout': '1' },
      }));
      const result = await client.POST('/api/v1/auth/logout', { headers: headers(context.csrf_token) });
      if (!result.response.ok) throw new ApiError(result.error?.error.code ?? 'unavailable');
    }),
    activity: (s: SessionState) => safe(async () => {
      const result = await client.POST('/api/v1/auth/activity', { headers: headers(s.csrf_token) });
      if (!result.response.ok) throw new ApiError(result.error?.error.code ?? 'unavailable');
    }),
    entities: (signal?: AbortSignal) => safe(async () => unwrap(await client.GET('/api/v1/businesses', { signal, headers: headers() }))),
    businessSettings: (s: Session, signal?: AbortSignal) => safe(async () => unwrap(await client.GET('/api/v1/businesses/{business_id}/settings', { params: { path: { business_id: s.entity.id } }, signal, headers: headers() }))),
    changeBusinessCurrency: (s: Session, body: Schemas['BusinessCurrencyChange']) => safe(async () => unwrap(await client.PATCH('/api/v1/businesses/{business_id}/settings/currency', { params: { path: { business_id: s.entity.id } }, body, headers: headers(s.csrf_token) }))),
    betaStatus: (signal?: AbortSignal) => safe(async () => unwrap(await client.GET('/api/v1/beta/status', { signal, headers: headers() }))),
    createBusiness: (s: SessionState, body: Schemas['BusinessCreateRequest']) => safe(async () => unwrap(await client.POST('/api/v1/businesses', { body, headers: headers(s.csrf_token) }))),
    bootstrapStatus: (request_id: string, signal?: AbortSignal) => safe(async () => unwrap(await client.GET('/api/v1/businesses/creation/{request_id}', { params: { path: { request_id } }, signal, headers: headers() }))),
    members: (s: Session, signal?: AbortSignal) => safe(async () => unwrap(await client.GET('/api/v1/businesses/{business_id}/members', { params: { path: { business_id: s.entity.id } }, signal, headers: headers() }))),
    invitations: (s: Session, signal?: AbortSignal) => safe(async () => unwrap(await client.GET('/api/v1/businesses/{business_id}/invitations', { params: { path: { business_id: s.entity.id } }, signal, headers: headers() }))),
    invite: (s: Session, body: Schemas['InviteRequest']) => safe(async () => unwrap(await client.POST('/api/v1/businesses/{business_id}/invitations', { params: { path: { business_id: s.entity.id } }, body, headers: headers(s.csrf_token) }))),
    changeMember: (s: Session, user_id: string, body: Schemas['MemberChange']) => safe(async () => {
      const result = await client.PATCH('/api/v1/businesses/{business_id}/members/{user_id}', { params: { path: { business_id: s.entity.id, user_id } }, body, headers: headers(s.csrf_token) });
      confirmEmpty(result);
    }),
    grantOwnership: (s: Session, user_id: string, authorization_version: number) => safe(async () => {
      const result = await client.POST('/api/v1/businesses/{business_id}/members/{user_id}/ownership', { params: { path: { business_id: s.entity.id, user_id } }, body: { authorization_version }, headers: headers(s.csrf_token) });
      confirmEmpty(result);
    }),
    revokeInvitation: (s: Session, invitation_id: string) => safe(async () => {
      const result = await client.DELETE('/api/v1/businesses/{business_id}/invitations/{invitation_id}', { params: { path: { business_id: s.entity.id, invitation_id } }, headers: headers(s.csrf_token) });
      confirmEmpty(result);
    }),
    previewInvitation: (s: SessionState, credential: string) => safe(async () => unwrap(await client.POST('/api/v1/invitations/preview', { body: { credential }, headers: headers(s.csrf_token) }))),
    respondInvitation: (s: SessionState, credential: string, accept: boolean) => safe(async () => unwrap(await client.POST('/api/v1/invitations/respond', { body: { credential, accept }, headers: headers(s.csrf_token) }))),
    selectEntity: (s: SessionState, entity_id: string) => safe(async () => unwrap(await client.POST('/api/v1/session/entity',
      { body: { entity_id }, headers: headers(s.csrf_token) }))),
    accounts: (session: Session, signal?: AbortSignal) => safe(async () => unwrap(await client.GET(
      '/api/v1/entities/{entity_id}/accounts',
      { params: { path: { entity_id: session.entity.id }, query: { active_only: false } }, signal, headers: headers() },
    ))),
    createAccount: (session: Session, body: AccountCreate) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/accounts',
      { params: { path: { entity_id: session.entity.id } }, body, headers: headers(session.csrf_token) },
    ))),
    updateAccount: (s: Session, id: string, body: Schemas['AccountUpdate']) => safe(async () => unwrap(await client.PATCH(
      '/api/v1/entities/{entity_id}/accounts/{record_id}', { ...mutationParams(s, id), body }))),
    deactivateAccount: (s: Session, id: string, baseline: string) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/accounts/{record_id}/deactivate', { ...mutationParams(s, id), body: { baseline } }))),
    categories: (s: Session, signal?: AbortSignal) => safe(async () => unwrap(await client.GET(
      '/api/v1/entities/{entity_id}/categories', listParams(s, signal)))),
    createCategory: (s: Session, body: Schemas['CategoryCreate']) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/categories', { ...createParams(s), body }))),
    updateCategory: (s: Session, id: string, body: Schemas['CategoryUpdate']) => safe(async () => unwrap(await client.PATCH(
      '/api/v1/entities/{entity_id}/categories/{record_id}', { ...mutationParams(s, id), body }))),
    deleteCategory: (s: Session, id: string, baseline: string) => safe(async () => unwrap(await client.DELETE(
      '/api/v1/entities/{entity_id}/categories/{record_id}', { ...mutationParams(s, id), body: { baseline } }))),
    payees: (s: Session, signal?: AbortSignal) => safe(async () => unwrap(await client.GET(
      '/api/v1/entities/{entity_id}/payees', listParams(s, signal)))),
    createPayee: (s: Session, body: Schemas['ManagedCreate']) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/payees', { ...createParams(s), body }))),
    updatePayee: (s: Session, id: string, body: Schemas['ManagedUpdate']) => safe(async () => unwrap(await client.PATCH(
      '/api/v1/entities/{entity_id}/payees/{record_id}', { ...mutationParams(s, id), body }))),
    removePayee: (s: Session, id: string, baseline: string) => safe(async () => unwrap(await client.DELETE(
      '/api/v1/entities/{entity_id}/payees/{record_id}', { ...mutationParams(s, id), body: { baseline } }))),
    mergePayees: (s: Session, id: string, body: Schemas['ManagedMerge']) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/payees/{record_id}/merge', { ...mutationParams(s, id), body }))),
    tags: (s: Session, signal?: AbortSignal) => safe(async () => unwrap(await client.GET(
      '/api/v1/entities/{entity_id}/tags', listParams(s, signal)))),
    vehicles: (s: Session, signal?: AbortSignal) => safe(async () => unwrap(await client.GET('/api/v1/entities/{entity_id}/vehicles', listParams(s, signal)))),
    createVehicle: (s: Session, body: Schemas['ManagedCreate']) => safe(async () => unwrap(await client.POST('/api/v1/entities/{entity_id}/vehicles', { ...createParams(s), body }))),
    updateVehicle: (s: Session, id: string, body: Schemas['ManagedUpdate']) => safe(async () => unwrap(await client.PATCH('/api/v1/entities/{entity_id}/vehicles/{record_id}', { ...mutationParams(s, id), body }))),
    removeVehicle: (s: Session, id: string, baseline: string) => safe(async () => unwrap(await client.DELETE('/api/v1/entities/{entity_id}/vehicles/{record_id}', { ...mutationParams(s, id), body: { baseline } }))),
    createTag: (s: Session, body: Schemas['ManagedCreate']) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/tags', { ...createParams(s), body }))),
    updateTag: (s: Session, id: string, body: Schemas['ManagedUpdate']) => safe(async () => unwrap(await client.PATCH(
      '/api/v1/entities/{entity_id}/tags/{record_id}', { ...mutationParams(s, id), body }))),
    removeTag: (s: Session, id: string, baseline: string) => safe(async () => unwrap(await client.DELETE(
      '/api/v1/entities/{entity_id}/tags/{record_id}', { ...mutationParams(s, id), body: { baseline } }))),
    mergeTags: (s: Session, id: string, body: Schemas['ManagedMerge']) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/tags/{record_id}/merge', { ...mutationParams(s, id), body }))),
    transactions: (s: Session, signal?: AbortSignal) => safe(async () => unwrap(await client.GET(
      '/api/v1/entities/{entity_id}/transactions', listParams(s, signal)))),
    createTransaction: (s: Session, body: Schemas['TransactionCreate']) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/transactions', { ...createParams(s), body }))),
    prepareTransactionCopy: (s: Session, id: string, baseline: string) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/transactions/{record_id}/copy-draft', { ...mutationParams(s, id), body: { baseline } }))),
    bulkUpdateTransactions: (s: Session, body: Schemas['TransactionBulkUpdate']) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/transactions/bulk/update', { ...createParams(s), body }))),
    bulkDeleteTransactions: (s: Session, body: Schemas['TransactionBulkSelection']) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/transactions/bulk/delete', { ...createParams(s), body }))),
    updateTransaction: (s: Session, id: string, body: Schemas['TransactionUpdate']) => safe(async () => unwrap(await client.PATCH(
      '/api/v1/entities/{entity_id}/transactions/{record_id}', { ...mutationParams(s, id), body }))),
    deleteTransaction: (s: Session, id: string, baseline: string) => safe(async () => unwrap(await client.DELETE(
      '/api/v1/entities/{entity_id}/transactions/{record_id}', { ...mutationParams(s, id), body: { baseline } }))),
    journals: (s: Session, signal?: AbortSignal) => safe(async () => unwrap(await client.GET(
      '/api/v1/entities/{entity_id}/journals', listParams(s, signal)))),
    createJournal: (s: Session, body: Schemas['JournalCreate']) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/journals', { ...createParams(s), body }))),
    ledger: (s: Session, filters: { start?: string; end?: string; kind?: 'All' | 'Income' | 'Expense' | 'Transfer' | 'Journal'; account_ids?: string[]; category_ids?: string[]; tag_ids?: string[] } = {}, signal?: AbortSignal) => safe(async () => unwrap(await client.GET(
      '/api/v1/entities/{entity_id}/ledger', { ...listParams(s, signal), params: { path: { entity_id: s.entity.id }, query: filters } }))),
    reports: (s: Session, filters: { start?: string; end?: string } = {}, signal?: AbortSignal) => safe(async () => unwrap(await client.GET(
      '/api/v1/entities/{entity_id}/reports/summary', { ...listParams(s, signal), params: { path: { entity_id: s.entity.id }, query: filters } }))),
    downloadCustomerExport: (s: Session, scope: 'metadata_only' | 'documents_inclusive', signal?: AbortSignal) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/exports/handoff', { ...createParams(s), body: { scope }, signal, parseAs: 'blob' }))),
    downloadTransactionsCsv: (s: Session, signal?: AbortSignal) => safe(async () => unwrap(await client.GET(
      '/api/v1/entities/{entity_id}/exports/transactions.csv', { ...listParams(s, signal), parseAs: 'blob' }))),
    downloadJournalPostingsCsv: (s: Session, signal?: AbortSignal) => safe(async () => unwrap(await client.GET(
      '/api/v1/entities/{entity_id}/exports/journal-postings.csv', { ...listParams(s, signal), parseAs: 'blob' }))),
    downloadSelectedLedgerCsv: (s: Session, entry_ids: string[], filters: { start?: string; end?: string; kind?: 'All' | 'Income' | 'Expense' | 'Transfer' | 'Journal'; account_ids?: string[]; category_ids?: string[]; tag_ids?: string[] } = {}, signal?: AbortSignal) => safe(async () => {
      const options = { params: { path: { entity_id: s.entity.id } }, headers: headers(s.csrf_token),
        body: { kind: 'All' as const, account_ids: [], category_ids: [], tag_ids: [], ...filters, entry_ids },
        signal, parseAs: 'blob' as const };
      return unwrap(await client.POST('/api/v1/entities/{entity_id}/ledger/selected.csv', options));
    }),
    updateJournal: (s: Session, id: string, body: Schemas['JournalUpdate']) => safe(async () => unwrap(await client.PATCH(
      '/api/v1/entities/{entity_id}/journals/{record_id}', { ...mutationParams(s, id), body }))),
    deleteJournal: (s: Session, id: string, baseline: string) => safe(async () => unwrap(await client.DELETE(
      '/api/v1/entities/{entity_id}/journals/{record_id}', { ...mutationParams(s, id), body: { baseline } }))),
    mileage: (s: Session, signal?: AbortSignal) => safe(async () => unwrap(await client.GET(
      '/api/v1/entities/{entity_id}/mileage', listParams(s, signal)))),
    mileageMaps: (s: Session, signal?: AbortSignal) => safe(async () => unwrap(await client.GET(
      '/api/v1/entities/{entity_id}/mileage/maps', listParams(s, signal)))),
    mapsQuotaStatus: (s: Session, signal?: AbortSignal) => safe(async () => unwrap(await client.GET(
      '/api/v1/entities/{entity_id}/maps/usage', listParams(s, signal)))),
    loadMapsQuota: (s: Session, refresh: boolean) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/maps/usage', { ...createParams(s), body: { refresh } }))),
    calculateMileageRoute: (s: Session, body: Schemas['MileageRouteRequest'], signal?: AbortSignal) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/mileage/routes', { ...createParams(s), body, signal }))),
    mileageTrip: (s: Session, id: string, signal?: AbortSignal) => safe(async () => unwrap(await client.GET(
      '/api/v1/entities/{entity_id}/mileage/{record_id}', { ...mutationParams(s, id), signal }))),
    createMileage: (s: Session, body: Schemas['MileageCreate']) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/mileage', { ...listParams(s), body, headers: headers(s.csrf_token) }))),
    updateMileage: (s: Session, id: string, body: Schemas['MileageUpdate']) => safe(async () => unwrap(await client.PATCH(
      '/api/v1/entities/{entity_id}/mileage/{record_id}', { ...mutationParams(s, id), body }))),
    deleteMileage: (s: Session, id: string, baseline: string) => safe(async () => unwrap(await client.DELETE(
      '/api/v1/entities/{entity_id}/mileage/{record_id}', { ...mutationParams(s, id), body: { baseline } }))),
    prepareTransactionCsv: (s: Session, file: File) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/imports/csv/prepare', { ...createParams(s),
        body: '', bodySerializer: () => file, headers: { ...headers(s.csrf_token), 'Content-Type': 'application/octet-stream' } }))),
    previewQuickBooksTransactions: (s: Session, file: File, auto_create: boolean) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/imports/quickbooks/preview', {
        params: { path: { entity_id: s.entity.id }, query: { filename: file.name, auto_create } },
        body: '', bodySerializer: () => file, headers: { ...headers(s.csrf_token), 'Content-Type': 'application/octet-stream' } }))),
    importTransactionCsv: (s: Session, file: File) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/imports/csv', { ...createParams(s),
        body: '', bodySerializer: () => file, headers: { ...headers(s.csrf_token), 'Content-Type': 'application/octet-stream' } }))),
    importQuickBooksTransactions: (s: Session, file: File, auto_create: boolean) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/imports/quickbooks', {
        params: { path: { entity_id: s.entity.id }, query: { filename: file.name, auto_create } },
        body: '', bodySerializer: () => file, headers: { ...headers(s.csrf_token), 'Content-Type': 'application/octet-stream' } }))),
    previewQuickBooksMileage: (s: Session, file: File) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/imports/quickbooks-mileage/preview', { ...createParams(s),
        body: '', bodySerializer: () => file, headers: { ...headers(s.csrf_token), 'Content-Type': 'application/octet-stream' } }))),
    importQuickBooksMileage: (s: Session, file: File) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/imports/quickbooks-mileage', { ...createParams(s),
        body: '', bodySerializer: () => file, headers: { ...headers(s.csrf_token), 'Content-Type': 'application/octet-stream' } }))),
    mixedUse: (s: Session, signal?: AbortSignal) => safe(async () => unwrap(await client.GET('/api/v1/entities/{entity_id}/mixed-use', listParams(s, signal)))),
    mixedUseSource: (s: Session, id: string, signal?: AbortSignal) => safe(async () => unwrap(await client.GET('/api/v1/entities/{entity_id}/mixed-use/{record_id}', { ...mutationParams(s, id), signal }))),
    mixedUseCopyDraft: (s: Session, id: string, signal?: AbortSignal) => safe(async () => unwrap(await client.POST('/api/v1/entities/{entity_id}/mixed-use/{record_id}/copy-draft', { ...mutationParams(s, id), signal }))),
    previewAllocation: (s: Session, body: Schemas['AllocationRequest'], signal?: AbortSignal) => safe(async () => unwrap(await client.POST('/api/v1/entities/{entity_id}/mixed-use/allocation', { ...createParams(s), body, signal }))),
    createMixedUse: (s: Session, body: Schemas['MixedUseCreate']) => safe(async () => unwrap(await client.POST('/api/v1/entities/{entity_id}/mixed-use', { ...createParams(s), body }))),
    updateMixedUse: (s: Session, id: string, body: Schemas['MixedUseUpdate']) => safe(async () => unwrap(await client.PATCH('/api/v1/entities/{entity_id}/mixed-use/{record_id}', { ...mutationParams(s, id), body }))),
    deleteMixedUse: (s: Session, id: string, baseline: string) => safe(async () => unwrap(await client.DELETE('/api/v1/entities/{entity_id}/mixed-use/{record_id}', { ...mutationParams(s, id), body: { baseline } }))),
    recurring: (s: Session, signal?: AbortSignal) => safe(async () => unwrap(await client.GET('/api/v1/entities/{entity_id}/recurring', listParams(s, signal)))),
    recurringSchedule: (s: Session, id: string, signal?: AbortSignal) => safe(async () => unwrap(await client.GET('/api/v1/entities/{entity_id}/recurring/{record_id}', { ...mutationParams(s, id), signal }))),
    createRecurring: (s: Session, body: Schemas['RecurringCreate']) => safe(async () => unwrap(await client.POST('/api/v1/entities/{entity_id}/recurring', { ...createParams(s), body }))),
    convertExpenseToRecurring: (s: Session, id: string, body: Schemas['ExpenseRecurringConversion']) => safe(async () => unwrap(await client.POST('/api/v1/entities/{entity_id}/recurring/from-transaction/{record_id}', { ...mutationParams(s, id), body }))),
    updateRecurring: (s: Session, id: string, body: Schemas['RecurringUpdate']) => safe(async () => unwrap(await client.PATCH('/api/v1/entities/{entity_id}/recurring/{record_id}', { ...mutationParams(s, id), body }))),
    transitionRecurring: (s: Session, id: string, baseline: string, transition: 'pause' | 'resume' | 'end') => safe(async () => unwrap(await client.POST('/api/v1/entities/{entity_id}/recurring/{record_id}/{transition}', { params: { path: { ...path(s, id), transition } }, headers: headers(s.csrf_token), body: { baseline } }))),
    deleteRecurring: (s: Session, id: string, baseline: string) => safe(async () => unwrap(await client.DELETE('/api/v1/entities/{entity_id}/recurring/{record_id}', { ...mutationParams(s, id), body: { baseline } }))),
    reconcileRecurring: (s: Session, body: Schemas['ReconcileRequest']) => safe(async () => unwrap(await client.POST('/api/v1/entities/{entity_id}/recurring/reconcile', { ...createParams(s), body }))),
    assets: (s: Session, signal?: AbortSignal) => safe(async () => unwrap(await client.GET('/api/v1/entities/{entity_id}/assets', listParams(s, signal)))),
    assetTypes: (s: Session, signal?: AbortSignal) => safe(async () => unwrap(await client.GET('/api/v1/entities/{entity_id}/assets/types', listParams(s, signal)))),
    asset: (s: Session, id: string, signal?: AbortSignal) => safe(async () => unwrap(await client.GET('/api/v1/entities/{entity_id}/assets/{record_id}', { ...mutationParams(s, id), signal }))),
    createAsset: (s: Session, body: Schemas['AssetCreate']) => safe(async () => unwrap(await client.POST('/api/v1/entities/{entity_id}/assets', { ...createParams(s), body }))),
    updateAsset: (s: Session, id: string, body: Schemas['AssetUpdate']) => safe(async () => unwrap(await client.PATCH('/api/v1/entities/{entity_id}/assets/{record_id}', { ...mutationParams(s, id), body }))),
    deleteAsset: (s: Session, id: string, baseline: string) => safe(async () => unwrap(await client.DELETE('/api/v1/entities/{entity_id}/assets/{record_id}', { ...mutationParams(s, id), body: { baseline } }))),
    postDepreciation: (s: Session, id: string, body: Schemas['AssetPost']) => safe(async () => unwrap(await client.POST('/api/v1/entities/{entity_id}/assets/{record_id}/post', { ...mutationParams(s, id), body }))),
    overrideDepreciation: (s: Session, id: string, sequence: number, body: Schemas['AssetOverride']) => safe(async () => unwrap(await client.PUT('/api/v1/entities/{entity_id}/assets/{record_id}/overrides/{sequence}', { params: { path: { ...path(s, id), sequence } }, headers: headers(s.csrf_token), body }))),
    removeDepreciationOverride: (s: Session, id: string, sequence: number, baseline: string) => safe(async () => unwrap(await client.DELETE('/api/v1/entities/{entity_id}/assets/{record_id}/overrides/{sequence}', { params: { path: { ...path(s, id), sequence } }, headers: headers(s.csrf_token), body: { baseline } }))),
    unreferencedDocuments: (s: Session, signal?: AbortSignal) => safe(async () => unwrap(await client.GET(
      '/api/v1/entities/{entity_id}/documents', listParams(s, signal)))),
    transactionDocuments: (s: Session, id: string, signal?: AbortSignal) => safe(async () => unwrap(await client.GET(
      '/api/v1/entities/{entity_id}/transactions/{record_id}/receipts', { ...mutationParams(s, id), signal }))),
    mixedUseDocuments: (s: Session, id: string, signal?: AbortSignal) => safe(async () => unwrap(await client.GET(
      '/api/v1/entities/{entity_id}/mixed-use/{record_id}/receipts', { ...mutationParams(s, id), signal }))),
    linkMixedUseDocument: (s: Session, id: string, body: Schemas['MixedUseDocumentLink']) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/mixed-use/{record_id}/receipts', { ...mutationParams(s, id), body }))),
    unlinkMixedUseDocument: (s: Session, id: string, document_id: string, baseline: string) => safe(async () => unwrap(await client.DELETE(
      '/api/v1/entities/{entity_id}/mixed-use/{record_id}/receipts/{document_id}', {
        params: { path: { entity_id: s.entity.id, record_id: id, document_id } }, headers: headers(s.csrf_token), body: { baseline },
      }))),
    uploadDocument: (s: Session, id: string, file: File) => safe(async () => unwrap(await client.PUT(
      '/api/v1/entities/{entity_id}/documents/{document_id}/content', {
        params: { path: { entity_id: s.entity.id, document_id: id }, query: { filename: file.name } },
        body: '', bodySerializer: () => file, headers: { ...headers(s.csrf_token), 'Content-Type': 'application/octet-stream' },
      }))),
    downloadDocument: (s: Session, document_id: string, signal?: AbortSignal) => safe(async () => unwrap(await client.GET(
      '/api/v1/entities/{entity_id}/documents/{document_id}/content', {
        params: { path: { entity_id: s.entity.id, document_id } }, headers: headers(), parseAs: 'blob', signal,
      }))),
    linkDocument: (s: Session, id: string, document_id: string) => safe(async () => unwrap(await client.POST(
      '/api/v1/entities/{entity_id}/transactions/{record_id}/receipts', { ...mutationParams(s, id), body: { document_id } }))),
    unlinkDocument: (s: Session, id: string, document_id: string) => safe(async () => unwrap(await client.DELETE(
      '/api/v1/entities/{entity_id}/transactions/{record_id}/receipts/{document_id}', {
        params: { path: { entity_id: s.entity.id, record_id: id, document_id } }, headers: headers(s.csrf_token),
      }))),
  };
}

export const api = createApi();
