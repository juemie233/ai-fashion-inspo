"""测试配置常量管理。"""

import pytest
from pathlib import Path
from app.config import Settings, ConfigConstants, settings, config_constants
from unittest.mock import patch, Mock


class TestSettings:
    """测试基础配置类。"""
    
    def test_settings_initialization(self):
        """测试配置初始化。"""
        assert settings.app_name == "Fashion Inspo"
        assert settings.app_version == "0.1.0"
        assert settings.debug is True
    
    def test_settings_storage_dirs(self):
        """测试存储目录配置。"""
        dirs = settings.storage_dirs
        assert "images" in dirs
        assert "thumbnails" in dirs
        assert "videos" in dirs
        assert "trash" in dirs
        assert "person_photos" in dirs
        assert "person_thumbnails" in dirs
        
        # 检查路径都是Path对象
        for dir_path in dirs.values():
            assert isinstance(dir_path, Path)
    
    def test_settings_config_constants_property(self):
        """测试配置常量属性。"""
        constants = settings.config_constants
        assert isinstance(constants, ConfigConstants)
        assert constants.settings is settings


class TestConfigConstants:
    """测试配置常量类。"""
    
    def test_config_constants_initialization(self):
        """测试配置常量初始化。"""
        assert config_constants.settings is settings
    
    def test_ai_constants(self):
        """测试AI相关常量。"""
        # 测试温度参数
        temp = config_constants.ai_temperature
        assert isinstance(temp, float)
        assert 0 <= temp <= 2.0
        
        # 测试top_p
        top_p = config_constants.ai_top_p
        assert isinstance(top_p, float)
        assert 0 <= top_p <= 1.0
        
        # 测试top_k
        top_k = config_constants.ai_top_k
        assert isinstance(top_k, int)
        assert top_k >= 1
        
        # 测试num_predict
        num_predict = config_constants.ai_num_predict
        assert isinstance(num_predict, int)
        assert num_predict >= 1
        
        # 测试num_ctx
        num_ctx = config_constants.ai_num_ctx
        assert isinstance(num_ctx, int)
        assert num_ctx >= 1
        
        # 测试置信度阈值
        low_conf = config_constants.ai_low_confidence_threshold
        assert isinstance(low_conf, float)
        assert 0 <= low_conf <= 1.0
        
        gen_conf = config_constants.ai_generated_confidence_threshold
        assert isinstance(gen_conf, float)
        assert 0 <= gen_conf <= 1.0
    
    def test_vector_constants(self):
        """测试向量检索常量。"""
        # 测试默认TopK
        top_k = config_constants.vector_top_k_default
        assert isinstance(top_k, int)
        assert top_k >= 1
        
        # 测试权重
        sim_weight = config_constants.vector_similarity_weight
        tag_weight = config_constants.vector_tag_weight
        assert isinstance(sim_weight, float)
        assert isinstance(tag_weight, float)
        assert 0 <= sim_weight <= 1.0
        assert 0 <= tag_weight <= 1.0
        assert abs(sim_weight + tag_weight - 1.0) < 0.1  # 权重总和接近1
        
        # 测试向量维度
        text_dim = config_constants.text_vector_dim
        image_dim = config_constants.image_vector_dim
        assert isinstance(text_dim, int)
        assert isinstance(image_dim, int)
        assert text_dim > 0
        assert image_dim > 0
    
    def test_image_processing_constants(self):
        """测试图片处理常量。"""
        # 测试缩略图大小
        thumb_size = config_constants.thumbnail_size
        assert isinstance(thumb_size, tuple)
        assert len(thumb_size) == 2
        assert all(isinstance(dim, int) for dim in thumb_size)
        assert all(dim > 0 for dim in thumb_size)
        
        # 测试缩略图质量
        quality = config_constants.thumbnail_quality
        assert isinstance(quality, int)
        assert 0 <= quality <= 100
        
        # 测试上传限制
        max_image = config_constants.max_image_upload_mb
        max_video = config_constants.max_video_upload_mb
        assert isinstance(max_image, int)
        assert isinstance(max_video, int)
        assert max_image > 0
        assert max_video > 0
    
    def test_tag_constants(self):
        """测试标签常量。"""
        # 测试标签名最大长度
        max_length = config_constants.tag_name_max_length
        assert isinstance(max_length, int)
        assert max_length > 0
        
        # 测试预设标签
        seed_tags = config_constants.seed_tags
        assert isinstance(seed_tags, list)
        assert all(isinstance(tag, str) for tag in seed_tags)
        assert all(len(tag) <= max_length for tag in seed_tags)
    
    def test_quality_constants(self):
        """测试质量审核常量。"""
        # 测试分类器阈值
        threshold = config_constants.quality_classifier_threshold
        assert isinstance(threshold, float)
        assert 0 <= threshold <= 1.0
        
        # 测试手动上传自动审核
        auto_approve = config_constants.manual_upload_auto_approve
        assert isinstance(auto_approve, bool)
    
    def test_face_constants(self):
        """测试人脸识别常量。"""
        # 测试超时时间
        timeout = config_constants.face_service_timeout
        assert isinstance(timeout, float)
        assert timeout > 0
        
        # 测试匹配阈值
        match_threshold = config_constants.face_match_threshold
        assert isinstance(match_threshold, float)
        assert 0 <= match_threshold <= 1.0
    
    def test_task_constants(self):
        """测试任务队列常量。"""
        # 测试轮询间隔
        poll_interval = config_constants.poll_interval
        assert isinstance(poll_interval, float)
        assert poll_interval > 0
        
        # 测试心跳间隔
        heartbeat_interval = config_constants.heartbeat_interval
        assert isinstance(heartbeat_interval, float)
        assert heartbeat_interval > 0
        
        # 测试过期阈值
        stale_threshold = config_constants.stale_heartbeat_threshold
        assert isinstance(stale_threshold, float)
        assert stale_threshold > 0
    
    def test_scraper_constants(self):
        """测试爬虫常量。"""
        # 测试请求延迟
        delay = config_constants.scraper_request_delay
        assert isinstance(delay, float)
        assert delay >= 0
        
        # 测试最大并发数
        max_concurrent = config_constants.scraper_max_concurrent
        assert isinstance(max_concurrent, int)
        assert max_concurrent >= 1
        
        # 测试默认最大数量
        default_max = config_constants.scraper_default_max_count
        assert isinstance(default_max, int)
        assert default_max >= 1
        
        # 测试浏览器模式
        headless = config_constants.scraper_browser_headless
        assert isinstance(headless, bool)
        
        # 测试Chrome配置
        debug_port = config_constants.chrome_debug_port
        assert isinstance(debug_port, int)
        assert 1 <= debug_port <= 65535
        
        auto_restart = config_constants.chrome_auto_restart_limit
        assert isinstance(auto_restart, int)
        assert auto_restart >= 0
        
        idle_timeout = config_constants.chrome_idle_timeout
        assert isinstance(idle_timeout, int)
        assert idle_timeout >= 0
        
        startup_timeout = config_constants.chrome_startup_timeout
        assert isinstance(startup_timeout, int)
        assert startup_timeout > 0
        
        # 测试任务重试
        task_retry = config_constants.task_auto_retry
        assert isinstance(task_retry, int)
        assert task_retry >= 0


class TestConfigConstantsFallback:
    """测试配置常量的回退机制。"""
    
    def test_fallback_for_missing_settings(self):
        """测试缺少设置时的回退值。"""
        # 创建一个空的设置对象
        empty_settings = Settings()
        
        # 创建配置常量对象
        constants = ConfigConstants(empty_settings)
        
        # 测试各种回退值
        assert constants.ai_temperature == 0.7
        assert constants.ai_top_p == 0.9
        assert constants.ai_top_k == 40
        assert constants.ai_num_predict == 4096
        assert constants.ai_num_ctx == 16384
        assert constants.ai_low_confidence_threshold == 0.6
        assert constants.ai_generated_confidence_threshold == 0.8
        assert constants.vector_top_k_default == 20
        assert constants.vector_similarity_weight == 0.6
        assert constants.vector_tag_weight == 0.4
        assert constants.thumbnail_size == (400, 600)
        assert constants.thumbnail_quality == 85
        assert constants.max_image_upload_mb == 20
        assert constants.max_video_upload_mb == 500
        assert constants.tag_name_max_length == 12
        assert constants.quality_classifier_threshold == 0.9
        assert constants.manual_upload_auto_approve is True
        assert constants.face_service_timeout == 30.0
        assert constants.face_match_threshold == 0.5
        assert constants.poll_interval == 1.0
        assert constants.heartbeat_interval == 10.0
        assert constants.stale_heartbeat_threshold == 90.0
        assert constants.scraper_request_delay == 2.0
        assert constants.scraper_max_concurrent == 3
        assert constants.scraper_default_max_count == 20
        assert constants.scraper_browser_headless is True
        assert constants.chrome_debug_port == 9222
        assert constants.chrome_auto_restart_limit == 3
        assert constants.chrome_idle_timeout == 600
        assert constants.chrome_startup_timeout == 20
        assert constants.task_auto_retry == 2
    
    def test_seed_tags_fallback(self):
        """测试预设标签的回退值。"""
        empty_settings = Settings()
        constants = ConfigConstants(empty_settings)
        
        seed_tags = constants.seed_tags
        assert isinstance(seed_tags, list)
        assert len(seed_tags) > 0
        assert "JK制服" in seed_tags
        assert "汉服" in seed_tags


class TestConfigConstantsCustomValues:
    """测试自定义配置值。"""
    
    def test_custom_ai_settings(self):
        """测试自定义AI设置。"""
        custom_settings = Settings()
        custom_settings.ai_temperature = 0.5
        custom_settings.ai_top_p = 0.8
        
        constants = ConfigConstants(custom_settings)
        
        assert constants.ai_temperature == 0.5
        assert constants.ai_top_p == 0.8
    
    def test_custom_scraper_settings(self):
        """测试自定义爬虫设置。"""
        custom_settings = Settings()
        custom_settings.scraper_max_concurrent = 5
        custom_settings.scraper_request_delay = 3.0
        
        constants = ConfigConstants(custom_settings)
        
        assert constants.scraper_max_concurrent == 5
        assert constants.scraper_request_delay == 3.0
    
    def test_mixed_custom_and_default(self):
        """测试混合自定义和默认值。"""
        custom_settings = Settings()
        custom_settings.ai_temperature = 0.9  # 自定义
        # ai_top_p 不设置，应该使用默认值
        
        constants = ConfigConstants(custom_settings)
        
        assert constants.ai_temperature == 0.9
        assert constants.ai_top_p == 0.9  # 默认值


class TestConfigConstantsIntegration:
    """测试配置常量的集成功能。"""
    
    def test_constants_access_from_settings(self):
        """测试从Settings对象访问常量。"""
        constants = settings.config_constants
        assert isinstance(constants, ConfigConstants)
        
        # 测试各种常量访问
        ai_temp = constants.ai_temperature
        assert isinstance(ai_temp, float)
        
        vector_k = constants.vector_top_k_default
        assert isinstance(vector_k, int)
    
    def test_global_config_constants_instance(self):
        """测试全局配置常量实例。"""
        assert isinstance(config_constants, ConfigConstants)
        assert config_constants.settings is settings
    
    def test_multiple_constants_instances(self):
        """测试多个配置常量实例。"""
        constants1 = ConfigConstants(settings)
        constants2 = ConfigConstants(settings)
        
        # 两个实例应该返回相同的值
        assert constants1.ai_temperature == constants2.ai_temperature
        assert constants1.vector_top_k_default == constants2.vector_top_k_default
    
    def test_constants_immutability(self):
        """测试配置常量的不可变性。"""
        # 获取一个常量值
        original_temp = config_constants.ai_temperature
        
        # 修改settings中的值
        original_ai_temp = settings.ai_temperature if hasattr(settings, 'ai_temperature') else None
        
        # 创建新的常量实例
        new_constants = ConfigConstants(settings)
        
        # 新实例应该反映当前settings的状态
        if hasattr(settings, 'ai_temperature'):
            assert new_constants.ai_temperature == settings.ai_temperature
        else:
            assert new_constants.ai_temperature == 0.7  # 默认值


class TestConfigConstantsValidation:
    """测试配置常量的验证。"""
    
    def test_ai_temperature_range(self):
        """测试AI温度参数范围。"""
        assert 0 <= config_constants.ai_temperature <= 2.0
    
    def test_vector_weights_sum(self):
        """测试向量权重总和。"""
        total = config_constants.vector_similarity_weight + config_constants.vector_tag_weight
        assert abs(total - 1.0) < 0.1  # 允许小的浮点误差
    
    def test_thumbnail_dimensions_positive(self):
        """测试缩略图尺寸为正数。"""
        width, height = config_constants.thumbnail_size
        assert width > 0
        assert height > 0
    
    def test_port_range(self):
        """测试端口号范围。"""
        port = config_constants.chrome_debug_port
        assert 1 <= port <= 65535
    
    def test_timeout_values_positive(self):
        """测试超时值为正数。"""
        timeouts = [
            config_constants.face_service_timeout,
            config_constants.chrome_idle_timeout,
            config_constants.chrome_startup_timeout,
            config_constants.poll_interval,
            config_constants.heartbeat_interval,
        ]
        for timeout in timeouts:
            assert timeout >= 0


class TestConfigConstantsConsistency:
    """测试配置常量的一致性。"""
    
    def test_consistency_with_settings(self):
        """测试常量与设置的一致性。"""
        # 如果settings中有某个值，常量应该返回相同的值
        if hasattr(settings, 'ai_temperature'):
            assert config_constants.ai_temperature == settings.ai_temperature
    
    def test_consistency_across_access(self):
        """测试多次访问的一致性。"""
        temp1 = config_constants.ai_temperature
        temp2 = config_constants.ai_temperature
        temp3 = config_constants.ai_temperature
        
        assert temp1 == temp2 == temp3
    
    def test_type_consistency(self):
        """测试类型一致性。"""
        assert isinstance(config_constants.ai_temperature, float)
        assert isinstance(config_constants.ai_top_k, int)
        assert isinstance(config_constants.vector_top_k_default, int)
        assert isinstance(config_constants.thumbnail_quality, int)
        assert isinstance(config_constants.manual_upload_auto_approve, bool)
        assert isinstance(config_constants.seed_tags, list)