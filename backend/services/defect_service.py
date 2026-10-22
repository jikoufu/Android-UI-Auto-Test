"""缺陷审核的实施状态；不伪造缺陷分析结果。"""

from backend.schemas import CapabilityStatus


def get_defect_status() -> CapabilityStatus:
    return CapabilityStatus(status="not_implemented", detail="缺陷管理、审核和外部缺陷系统同步尚未实现。")
