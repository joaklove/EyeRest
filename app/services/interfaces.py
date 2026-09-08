"""预留扩展接口定义（V2+）。

本模块定义 V1 阶段暂不实现、但预留的扩展服务接口。
所有接口均为抽象基类（ABC），V1 不提供具体实现，
方法默认抛出 :class:`NotImplementedError`，确保不影响核心逻辑运行。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class DisplayService(ABC):
    """显示器管理服务（V2+）。

    预期职责：调节屏幕亮度、枚举显示器、多显示器支持等。
    """

    @abstractmethod
    def set_brightness(self, level: int) -> bool:
        """设置屏幕亮度 0-100。

        Args:
            level: 亮度级别，取值范围 0-100。

        Returns:
            是否设置成功。
        """
        raise NotImplementedError

    @abstractmethod
    def get_brightness(self) -> int:
        """获取当前亮度。

        Returns:
            当前亮度值（0-100）。
        """
        raise NotImplementedError

    @abstractmethod
    def get_displays(self) -> list:
        """获取所有显示器信息。

        Returns:
            显示器信息列表。
        """
        raise NotImplementedError


class CameraService(ABC):
    """摄像头检测服务（V2+）。

    预期职责：摄像头帧捕获、人脸距离检测、用眼姿态分析等。
    """

    @abstractmethod
    def capture_frame(self):
        """捕获一帧图像。

        Returns:
            捕获到的图像帧（具体类型由实现决定）。
        """
        raise NotImplementedError

    @abstractmethod
    def detect_face_distance(self) -> float:
        """检测人脸距离（米）。

        Returns:
            人脸到摄像头的距离，单位为米。
        """
        raise NotImplementedError


class FullscreenService(ABC):
    """全屏检测服务（V2+）。

    预期职责：检测前台窗口是否全屏、识别全屏应用名称。
    """

    @abstractmethod
    def is_fullscreen(self) -> bool:
        """当前是否全屏。

        Returns:
            若当前前台窗口为全屏则返回 ``True``，否则返回 ``False``。
        """
        raise NotImplementedError

    @abstractmethod
    def get_fullscreen_app(self) -> str:
        """获取全屏应用名称。

        Returns:
            当前全屏应用的名称；若未全屏则返回空字符串。
        """
        raise NotImplementedError


class HealthAnalysisService(ABC):
    """健康分析服务（V2+）。

    预期职责：基于统计数据分析用眼疲劳、生成健康建议。
    """

    @abstractmethod
    def analyze_eye_strain(self, daily_stats) -> dict:
        """分析用眼疲劳程度。

        Args:
            daily_stats: 当日用眼统计数据。

        Returns:
            包含疲劳等级、各项指标的分析结果字典。
        """
        raise NotImplementedError

    @abstractmethod
    def get_recommendations(self) -> list:
        """获取健康建议。

        Returns:
            健康建议列表。
        """
        raise NotImplementedError


class CloudService(ABC):
    """云同步服务（V2+）。

    预期职责：设置云同步、统计数据同步、云端备份管理。
    """

    @abstractmethod
    def sync_settings(self) -> bool:
        """同步设置到云端。

        Returns:
            是否同步成功。
        """
        raise NotImplementedError

    @abstractmethod
    def sync_stats(self) -> bool:
        """同步统计数据到云端。

        Returns:
            是否同步成功。
        """
        raise NotImplementedError

    @abstractmethod
    def get_backup(self) -> dict:
        """获取云端备份。

        Returns:
            云端备份数据字典。
        """
        raise NotImplementedError
