"""Base class for scheduler jobs."""

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from ...common.database import get_database, SchedulerRunDB

logger = logging.getLogger(__name__)


class BaseJob(ABC):
    """Base class for all scheduler jobs.

    Provides:
    - Execution logging to database
    - Dry run support
    - Error handling with alerts
    """

    job_name: str = "base_job"

    def __init__(self, dry_run: bool = False):
        """Initialize job.

        Args:
            dry_run: If True, simulate actions without executing
        """
        self.dry_run = dry_run
        self.db = get_database()
        self._run_id: Optional[int] = None

    def run(self, **kwargs) -> dict:
        """Execute the job with logging.

        Args:
            **kwargs: Job-specific arguments

        Returns:
            Dict with job results
        """
        self._start_run(kwargs.get("sport"))

        try:
            result = self.execute(**kwargs)
            self._complete_run(
                items_processed=result.get("items_processed", 0),
                details=result,
            )
            return result

        except Exception as e:
            logger.error(f"Job {self.job_name} failed: {e}")
            self._fail_run(str(e))
            raise

    @abstractmethod
    def execute(self, **kwargs) -> dict:
        """Execute the job logic.

        Args:
            **kwargs: Job-specific arguments

        Returns:
            Dict with job results
        """
        pass

    def _start_run(self, sport: Optional[str] = None) -> None:
        """Log job start to database."""
        session = self.db.get_session()
        try:
            run = SchedulerRunDB(
                job_name=self.job_name,
                sport=sport,
                status="started",
                started_at=datetime.utcnow(),
            )
            session.add(run)
            session.commit()
            self._run_id = run.id
            logger.info(f"Started job: {self.job_name} (run_id: {self._run_id})")

        except Exception as e:
            session.rollback()
            logger.warning(f"Failed to log job start: {e}")
        finally:
            session.close()

    def _complete_run(
        self,
        items_processed: int = 0,
        details: Optional[dict] = None,
    ) -> None:
        """Log job completion to database."""
        if not self._run_id:
            return

        session = self.db.get_session()
        try:
            run = session.query(SchedulerRunDB).filter_by(id=self._run_id).first()
            if run:
                run.status = "completed"
                run.completed_at = datetime.utcnow()
                run.duration_seconds = (
                    run.completed_at - run.started_at
                ).total_seconds()
                run.items_processed = items_processed
                if details:
                    run.details = json.dumps(details)
                session.commit()
                logger.info(
                    f"Completed job: {self.job_name} "
                    f"({items_processed} items in {run.duration_seconds:.1f}s)"
                )

        except Exception as e:
            session.rollback()
            logger.warning(f"Failed to log job completion: {e}")
        finally:
            session.close()

    def _fail_run(self, error_message: str) -> None:
        """Log job failure to database."""
        if not self._run_id:
            return

        session = self.db.get_session()
        try:
            run = session.query(SchedulerRunDB).filter_by(id=self._run_id).first()
            if run:
                run.status = "failed"
                run.completed_at = datetime.utcnow()
                run.duration_seconds = (
                    run.completed_at - run.started_at
                ).total_seconds()
                run.error_message = error_message
                session.commit()

        except Exception as e:
            session.rollback()
            logger.warning(f"Failed to log job failure: {e}")
        finally:
            session.close()
