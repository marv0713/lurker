from dataclasses import dataclass


@dataclass
class DailyReport:
    """A structured representation of the daily report."""
    report_date: str
    main_candidates_count: int
    content_md: str
    
    @property
    def push_title(self) -> str:
        if self.main_candidates_count > 0:
            return f"[{self.main_candidates_count}个主候选] Lurker 雷达 ({self.report_date})"
        return f"[无新信号] Lurker 雷达 ({self.report_date})"
