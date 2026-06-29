"""
providers/base.py

The ScheduleProvider interface. Every data source (K-Electric, the PITC
CCMS portal, anything we add later) implements this exact contract.

Why an abstract base class
---------------------------
The whole point of "no single point of failure" is that the registry
(core/registry.py) can loop over a list of providers and call the SAME
method on each one, without knowing or caring how any individual provider
gets its data. That uniformity is only possible if every provider commits
to the same method signature and return type.

C++ analogy
-----------
This is a pure virtual base class:

    class ScheduleProvider {
    public:
        virtual ProviderResult fetch() = 0;
        virtual std::string name() const = 0;
        virtual ~ScheduleProvider() = default;
    };

    class KElectricProvider : public ScheduleProvider { ... };
    class PitcCcmsProvider  : public ScheduleProvider { ... };

Python doesn't enforce method implementation at compile time the way C++
does with `= 0`, but `abc.ABC` + `@abstractmethod` gets you the closest
runtime equivalent: instantiating a subclass that hasn't implemented
`fetch()` raises TypeError immediately, instead of failing later with a
confusing AttributeError deep inside the registry loop.

Common mistake
--------------
Don't let fetch() raise on network/parsing errors. Catch everything
inside the provider and return a ProviderResult with status=FAILED and
a useful `error` string. If fetch() raises, the *registry* has to know
about every possible exception type from every provider, which defeats
the purpose of the abstraction. Fail inside, report outside.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.models import ProviderResult


class ScheduleProvider(ABC):
    """Base class for every load-shedding data source."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short, stable identifier used in logs and JSON output,
        e.g. 'k_electric' or 'pitc_ccms'. Not a display name."""
        raise NotImplementedError

    @abstractmethod
    def fetch(self) -> ProviderResult:
        """Get this source's current schedule data and return it already
        normalized into a ProviderResult. Must not raise -- see module
        docstring above."""
        raise NotImplementedError
