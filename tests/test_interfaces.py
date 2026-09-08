"""预留扩展接口测试。

验证：
- 5 个接口类均可被导入
- 每个接口类都是 ABC，不能直接实例化
- 每个抽象方法的默认实现抛出 NotImplementedError
"""

from __future__ import annotations

import unittest
from abc import ABC

from app.services.interfaces import (
    CameraService,
    CloudService,
    DisplayService,
    FullscreenService,
    HealthAnalysisService,
)

ALL_INTERFACES = [
    DisplayService,
    CameraService,
    FullscreenService,
    HealthAnalysisService,
    CloudService,
]


class TestInterfaceImports(unittest.TestCase):
    """验证 5 个接口类均可被导入且为 ABC。"""

    def test_all_interfaces_importable(self) -> None:
        self.assertEqual(len(ALL_INTERFACES), 5)
        for cls in ALL_INTERFACES:
            self.assertTrue(issubclass(cls, ABC), f"{cls.__name__} 应继承自 ABC")

    def test_interface_names(self) -> None:
        names = {cls.__name__ for cls in ALL_INTERFACES}
        self.assertEqual(
            names,
            {
                "DisplayService",
                "CameraService",
                "FullscreenService",
                "HealthAnalysisService",
                "CloudService",
            },
        )


class TestInterfaceNotInstantiable(unittest.TestCase):
    """验证接口类不能直接实例化。"""

    def test_cannot_instantiate_display_service(self) -> None:
        with self.assertRaises(TypeError):
            DisplayService()  # type: ignore[abstract]

    def test_cannot_instantiate_camera_service(self) -> None:
        with self.assertRaises(TypeError):
            CameraService()  # type: ignore[abstract]

    def test_cannot_instantiate_fullscreen_service(self) -> None:
        with self.assertRaises(TypeError):
            FullscreenService()  # type: ignore[abstract]

    def test_cannot_instantiate_health_analysis_service(self) -> None:
        with self.assertRaises(TypeError):
            HealthAnalysisService()  # type: ignore[abstract]

    def test_cannot_instantiate_cloud_service(self) -> None:
        with self.assertRaises(TypeError):
            CloudService()  # type: ignore[abstract]


class TestDisplayServiceMethods(unittest.TestCase):
    """验证 DisplayService 方法抛 NotImplementedError。"""

    def _make_concrete(self) -> DisplayService:
        class _Impl(DisplayService):
            def set_brightness(self, level: int) -> bool:
                return super().set_brightness(level)

            def get_brightness(self) -> int:
                return super().get_brightness()

            def get_displays(self) -> list:
                return super().get_displays()

        return _Impl()

    def test_set_brightness_raises(self) -> None:
        with self.assertRaises(NotImplementedError):
            self._make_concrete().set_brightness(50)

    def test_get_brightness_raises(self) -> None:
        with self.assertRaises(NotImplementedError):
            self._make_concrete().get_brightness()

    def test_get_displays_raises(self) -> None:
        with self.assertRaises(NotImplementedError):
            self._make_concrete().get_displays()


class TestCameraServiceMethods(unittest.TestCase):
    """验证 CameraService 方法抛 NotImplementedError。"""

    def _make_concrete(self) -> CameraService:
        class _Impl(CameraService):
            def capture_frame(self):
                return super().capture_frame()

            def detect_face_distance(self) -> float:
                return super().detect_face_distance()

        return _Impl()

    def test_capture_frame_raises(self) -> None:
        with self.assertRaises(NotImplementedError):
            self._make_concrete().capture_frame()

    def test_detect_face_distance_raises(self) -> None:
        with self.assertRaises(NotImplementedError):
            self._make_concrete().detect_face_distance()


class TestFullscreenServiceMethods(unittest.TestCase):
    """验证 FullscreenService 方法抛 NotImplementedError。"""

    def _make_concrete(self) -> FullscreenService:
        class _Impl(FullscreenService):
            def is_fullscreen(self) -> bool:
                return super().is_fullscreen()

            def get_fullscreen_app(self) -> str:
                return super().get_fullscreen_app()

        return _Impl()

    def test_is_fullscreen_raises(self) -> None:
        with self.assertRaises(NotImplementedError):
            self._make_concrete().is_fullscreen()

    def test_get_fullscreen_app_raises(self) -> None:
        with self.assertRaises(NotImplementedError):
            self._make_concrete().get_fullscreen_app()


class TestHealthAnalysisServiceMethods(unittest.TestCase):
    """验证 HealthAnalysisService 方法抛 NotImplementedError。"""

    def _make_concrete(self) -> HealthAnalysisService:
        class _Impl(HealthAnalysisService):
            def analyze_eye_strain(self, daily_stats) -> dict:
                return super().analyze_eye_strain(daily_stats)

            def get_recommendations(self) -> list:
                return super().get_recommendations()

        return _Impl()

    def test_analyze_eye_strain_raises(self) -> None:
        with self.assertRaises(NotImplementedError):
            self._make_concrete().analyze_eye_strain({})

    def test_get_recommendations_raises(self) -> None:
        with self.assertRaises(NotImplementedError):
            self._make_concrete().get_recommendations()


class TestCloudServiceMethods(unittest.TestCase):
    """验证 CloudService 方法抛 NotImplementedError。"""

    def _make_concrete(self) -> CloudService:
        class _Impl(CloudService):
            def sync_settings(self) -> bool:
                return super().sync_settings()

            def sync_stats(self) -> bool:
                return super().sync_stats()

            def get_backup(self) -> dict:
                return super().get_backup()

        return _Impl()

    def test_sync_settings_raises(self) -> None:
        with self.assertRaises(NotImplementedError):
            self._make_concrete().sync_settings()

    def test_sync_stats_raises(self) -> None:
        with self.assertRaises(NotImplementedError):
            self._make_concrete().sync_stats()

    def test_get_backup_raises(self) -> None:
        with self.assertRaises(NotImplementedError):
            self._make_concrete().get_backup()


if __name__ == "__main__":
    unittest.main()
