"""Abstract base class for projection sources."""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

import pandas as pd

from ...common.models import Projection, Sport


class ProjectionSource(ABC):
    """Abstract base class for all projection data sources."""

    def __init__(self, name: str, weight: float = 1.0):
        """Initialize projection source.

        Args:
            name: Unique identifier for this source
            weight: Weight for this source when aggregating (0.0-1.0)
        """
        self.name = name
        self.weight = weight

    @abstractmethod
    def fetch_projections(
        self,
        sport: Sport,
        slate_date: Optional[datetime] = None,
    ) -> list[Projection]:
        """Fetch projections for a sport/slate.

        Args:
            sport: Sport to fetch projections for
            slate_date: Date of the slate (defaults to today)

        Returns:
            List of Projection objects

        Raises:
            ProjectionFetchError: If fetch fails
        """
        pass

    @abstractmethod
    def is_available(self, sport: Sport) -> bool:
        """Check if this source has projections available for a sport.

        Args:
            sport: Sport to check

        Returns:
            True if projections are available
        """
        pass

    def to_dataframe(self, projections: list[Projection]) -> pd.DataFrame:
        """Convert projections to pandas DataFrame.

        Args:
            projections: List of Projection objects

        Returns:
            DataFrame with projection data
        """
        return pd.DataFrame([p.model_dump() for p in projections])

    def from_dataframe(self, df: pd.DataFrame) -> list[Projection]:
        """Convert DataFrame to list of Projections.

        Args:
            df: DataFrame with projection columns

        Returns:
            List of Projection objects
        """
        projections = []
        for _, row in df.iterrows():
            projections.append(Projection(
                name=row.get("name", ""),
                team=row.get("team", ""),
                position=row.get("position", ""),
                source=self.name,
                projected_points=row.get("projected_points", 0.0),
                projected_ownership=row.get("projected_ownership"),
                floor=row.get("floor"),
                ceiling=row.get("ceiling"),
            ))
        return projections

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name}, weight={self.weight})"
