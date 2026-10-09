"""实验窗口接口复用生产纯窗口算法；保留归档异常契约。"""

from app.runtime.dispatch_windows import ObservedDependency, StartWindow, observed_window

__all__ = ["ObservedDependency", "StartWindow", "observed_window", "DispatchFailure"]


class DispatchFailure(ValueError):
    def __init__(self, message: str, detail: dict[str, object]) -> None:
        super().__init__(message)
        self.detail = detail
