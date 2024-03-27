from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any
from typing_extensions import deprecated

import numpy as np
import numpy.typing as npt

from ConfigSpace import c_util
from ConfigSpace.conditions import NotSet
from ConfigSpace.exceptions import IllegalValueError
from ConfigSpace.hyperparameters import FloatHyperparameter

if TYPE_CHECKING:
    from ConfigSpace.configuration_space import ConfigurationSpace


class Configuration(Mapping[str, Any]):
    def __init__(
        self,
        configuration_space: ConfigurationSpace,
        values: Mapping[str, Any] | None = None,
        vector: Sequence[float] | np.ndarray | None = None,
        allow_inactive_with_values: bool = False,
        origin: Any | None = None,
        config_id: int | None = None,
    ) -> None:
        """Class for a single configuration.

        The :class:`~ConfigSpace.configuration_space.Configuration` object holds
        for all active hyperparameters a value. While the
        :class:`~ConfigSpace.configuration_space.ConfigurationSpace` stores the
        definitions for the hyperparameters (value ranges, constraints,...), a
        :class:`~ConfigSpace.configuration_space.Configuration` object is
        more an instance of it. Parameters of a
        :class:`~ConfigSpace.configuration_space.Configuration` object can be
        accessed and modified similar to python dictionaries
        (c.f. :ref:`Guide<1st_Example>`).

        Args:
            configuration_space:
                The space this configuration is in
            values:
                A dictionary with pairs (hyperparameter_name, value), where value is
                a legal value of the hyperparameter in the above configuration_space
            vector:
                A numpy array for efficient representation. Either values or vector
                has to be given
            allow_inactive_with_values:
                Whether an Exception will be raised if a value for an inactive
                hyperparameter is given. Default is to raise an Exception.
                Default to False
            origin:
                Store information about the origin of this configuration.
                Defaults to None.
            config_id:
                Integer configuration ID which can be used by a program using the
                ConfigSpace package.
        """
        if (
            values is not None
            and vector is not None
            or values is None
            and vector is None
        ):
            raise ValueError(
                "Specify Configuration as either a dictionary or a vector.",
            )

        self.config_space = configuration_space
        self.allow_inactive_with_values = allow_inactive_with_values
        self.origin = origin
        self.config_id = config_id

        # This is cached. When it's None, it means it needs to be relaoaded
        # which is primarly handled in __getitem__.
        self._values: dict[str, Any] | None = None

        # Will be set below
        self._vector: np.ndarray

        if values is not None:
            unknown_keys = values.keys() - self.config_space.keys()
            if any(unknown_keys):
                raise ValueError(f"Unknown hyperparameter(s) {unknown_keys}")

            # Using cs._hyperparameters to iterate makes sure that the hyperparameters
            # in the configuration are sorted in the same way as they are sorted in
            # the configuration space
            self._values = {}
            self._vector = np.empty(shape=len(configuration_space), dtype=np.float64)

            for key, hp in configuration_space.items():
                i = configuration_space.index_of[key]

                value = values.get(key, NotSet)
                if value is NotSet:
                    self._vector[i] = np.nan
                    continue

                if not hp.legal_value(value):
                    raise IllegalValueError(hp, value)

                # Truncate the float to be of constant length for a python version
                if isinstance(hp, FloatHyperparameter):
                    value = float(np.round(value, 16))  # type: ignore

                self._values[key] = value
                self._vector[i] = hp.to_vector(value)  # type: ignore

            self.is_valid_configuration()

        elif vector is not None:
            _vector = np.asarray(vector, dtype=np.float64)

            # If we have a 2d array with shape (n, 1), flatten it
            if len(_vector.shape) == 2 and _vector.shape[1] == 1:
                _vector = _vector.flatten()

            if len(_vector.shape) > 1:
                raise ValueError(
                    "Only 1d arrays can be converted to a Configuration, "
                    f"you passed an array of shape {_vector.shape}",
                )

            n_hyperparameters = len(self.config_space)
            if len(_vector) != n_hyperparameters:
                raise ValueError(
                    f"Expected array of length {n_hyperparameters}, got {len(_vector)}",
                )

            self._vector = _vector

    def is_valid_configuration(self) -> None:
        """Check if the object is a valid.

        Raises:
        ------
        ValueError: If configuration is not valid.
        """
        c_util.check_configuration(
            self.config_space,
            self._vector,
            allow_inactive_with_values=self.allow_inactive_with_values,
        )

    def get_array(self) -> npt.NDArray[np.float64]:
        """The internal vector representation of this config.

        All continuous values are scaled between zero and one.

        Returns:
        -------
        numpy.ndarray
            The vector representation of the configuration
        """
        return self._vector

    def __contains__(self, item: object) -> bool:
        if not isinstance(item, str):
            return False

        return item in self

    def __setitem__(self, key: str, value: Any) -> None:
        param = self.config_space[key]
        if not param.legal_value(value):
            raise IllegalValueError(param, value)

        idx = self.config_space.index_of[key]

        # Recalculate the vector with respect to this new value
        vector_value = param.to_vector(value)
        new_array = c_util.change_hp_value(
            self.config_space,
            self.get_array().copy(),
            param.name,
            vector_value,
            idx,
        )
        c_util.check_configuration(self.config_space, new_array, False)

        # Reset cached items
        self._vector = new_array
        self._values = None

    def __getitem__(self, key: str) -> Any:
        if self._values is not None and key in self._values:
            return self._values[key]

        if key not in self.config_space:
            raise KeyError(key)

        item_idx = self.config_space.index_of[key]
        vector = self._vector[item_idx]
        if np.isnan(vector):
            # NOTE: Techinically we could raise an `InactiveHyperparameterError` here
            # but that causes the `.get()` method from being a mapping to fail.
            # Normally `config.get(key)`, if it fails, will return None. Apparently,
            # this only works if `__getitem__[]` raises a KeyError or something derived
            # from it.
            raise KeyError(key)

        hyperparameter = self.config_space[key]
        value = hyperparameter.to_value(vector)

        # Truncate float to be of constant length for a python version
        if isinstance(hyperparameter, FloatHyperparameter):
            value = float(np.round(value, 16))  # type: ignore

        if self._values is None:
            self._values = {}

        self._values[key] = value
        return value

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, self.__class__):
            return dict(self) == dict(other) and self.config_space == other.config_space
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.__repr__())

    def __repr__(self) -> str:
        values = dict(self)
        header = "Configuration(values={"
        lines = [f"  '{key}': {values[key]!r}," for key in sorted(values.keys())]
        end = "})"
        return "\n".join([header, *lines, end])

    def __iter__(self) -> Iterator[str]:
        for key in self.config_space:
            idx = self.config_space.index_of[key]
            if not np.isnan(self._vector[idx]):
                yield key

    def __len__(self) -> int:
        return len(self.config_space)

    # ------------ Marked Deprecated --------------------
    # Probably best to only remove these once we actually
    # make some other breaking changes
    # * Search `Marked Deprecated` to find others
    @deprecated(
        "Please use `dict(config)` instead of `config.get_dictionary()`"
        " or use it as a dictionary directly if needed.",
    )
    def get_dictionary(self) -> dict[str, Any]:
        """A representation of the `ConfigSpace.configuration_space.Configuration`
        in dictionary form.

        Retuns:
            Configuration as dictionary
        """
        return dict(self)

    # ---------------------------------------------------
