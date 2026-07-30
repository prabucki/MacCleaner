"""
Cleanup modules.

Every file in this package is imported at startup so its ``@cleanup_module`` decorators
register. Modules declare work through the DSL in :mod:`mc.registry`; none of them
delete anything directly, and none of them decide policy — that lives in
:mod:`mc.policy` and is enforced in :mod:`mc.runtime`.
"""
