"""Fixed action policy shared by HTTP and future trusted worker/admin adapters.

Capabilities are necessary authority, never approval to execute recovery.
Platform/operator and one-execution approval are intentionally absent from
tenant roles; privileged support and recovery require separate operator approval.
"""

from types import MappingProxyType

from .identity import IdentityError


READ = frozenset({'bookkeeping:read'})
WRITE = READ | {'bookkeeping:write'}
RECOVERY = READ | {'recovery:authorize', 'platform:operate', 'recovery:approved'}
ACTION_CAPABILITIES = MappingProxyType({
    'read': READ,
    'write': WRITE,
    'asset_depreciate': WRITE | {'asset:depreciate'},
    'period_close': WRITE | {'period:close'},
    'period_unlock': WRITE | {'period:unlock'},
    'entry_correct': WRITE | {'bookkeeping:correct'},
    'import': WRITE | {'bookkeeping:import'},
    'export': READ | {'bookkeeping:export'},
    'entity_manage': READ | {'entity:manage'},
    'organization_manage': READ | {'organization:manage'},
    'members_manage': READ | {'members:manage'},
    'ownership_manage': READ | {'ownership:manage'},
    'connections_manage': READ | {'connections:manage'},
    'backup_create': READ | {'backup:create'},
    'backup_list': READ | {'backup:list'},
    'backup_download': READ | {'backup:download'},
    'archive_recover': RECOVERY | {'archive:recover'},
    'restore': RECOVERY | {'restore:execute'},
    'migration': RECOVERY | {'migration:execute'},
    'cutover': RECOVERY | {'cutover:execute'},
    'ledger_reset': RECOVERY | {'ledger:reset'},
    'business_admin': READ | {'business:admin'},
    'audit_inspect': frozenset({'platform:operate', 'audit:inspect'}),
    'legacy_admin': frozenset({'local:admin'}),
})


def authorize_action(granted, action):
    """Unknown actions fail closed, including on future non-HTTP adapters."""
    required = ACTION_CAPABILITIES.get(action)
    if required is None or not isinstance(granted, frozenset) or not required <= granted:
        raise IdentityError('forbidden')
