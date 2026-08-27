"""测试自定义异常处理。"""

import pytest
from app.exceptions import (
    AppException,
    NotFoundException,
    ValidationException,
    ProcessingException,
    FileException,
    DatabaseException,
    AIException,
    ScraperException,
    TaskException,
    ConfigException,
    not_found,
    validation_error,
    processing_error,
    file_error,
    database_error,
    ai_error,
    scraper_error,
    task_error,
    config_error,
)


class TestAppException:
    """测试基础异常类。"""
    
    def test_app_exception_creation(self):
        """测试基础异常创建。"""
        exc = AppException("测试消息")
        assert exc.message == "测试消息"
        assert exc.details == {}
        assert str(exc) == "测试消息"
    
    def test_app_exception_with_details(self):
        """测试带详细信息的基础异常。"""
        details = {"code": 404, "resource": "test"}
        exc = AppException("测试消息", details)
        assert exc.message == "测试消息"
        assert exc.details == details


class TestNotFoundException:
    """测试资源未找到异常。"""
    
    def test_not_found_exception_creation(self):
        """测试资源未找到异常创建。"""
        exc = NotFoundException("Inspiration", "test-id")
        assert "Inspiration" in exc.message
        assert "test-id" in exc.message
        assert exc.resource_type == "Inspiration"
        assert exc.resource_id == "test-id"
    
    def test_not_found_exception_with_details(self):
        """测试带详细信息的资源未找到异常。"""
        details = {"additional": "info"}
        exc = NotFoundException("Tag", "tag-id", details)
        assert exc.details == details


class TestValidationException:
    """测试验证异常。"""
    
    def test_validation_exception_creation(self):
        """测试验证异常创建。"""
        exc = ValidationException("email", "邮箱格式不正确")
        assert "email" in exc.message
        assert "邮箱格式不正确" in exc.message
        assert exc.field == "email"
        assert exc.message == "字段 email 验证失败: 邮箱格式不正确"
    
    def test_validation_exception_with_details(self):
        """测试带详细信息的验证异常。"""
        details = {"expected_format": "user@example.com"}
        exc = ValidationException("username", "用户名格式错误", details)
        assert exc.details == details


class TestHelperFunctions:
    """测试异常辅助函数。"""
    
    def test_not_found_helper(self):
        """测试快速创建资源未找到异常的辅助函数。"""
        exc = not_found("Model", "model-id")
        assert isinstance(exc, NotFoundException)
        assert exc.resource_type == "Model"
        assert exc.resource_id == "model-id"
    
    def test_validation_error_helper(self):
        """测试快速创建验证异常的辅助函数。"""
        exc = validation_error("password", "密码长度不足")
        assert isinstance(exc, ValidationException)
        assert exc.field == "password"
        assert "密码长度不足" in exc.message
    
    def test_processing_error_helper(self):
        """测试快速创建处理异常的辅助函数。"""
        exc = processing_error("处理失败，请重试")
        assert isinstance(exc, ProcessingException)
        assert exc.message == "处理失败，请重试"
    
    def test_file_error_helper(self):
        """测试快速创建文件异常的辅助函数。"""
        exc = file_error("文件不存在")
        assert isinstance(exc, FileException)
        assert exc.message == "文件不存在"
    
    def test_database_error_helper(self):
        """测试快速创建数据库异常的辅助函数。"""
        exc = database_error("数据库连接失败")
        assert isinstance(exc, DatabaseException)
        assert exc.message == "数据库连接失败"
    
    def test_ai_error_helper(self):
        """测试快速创建AI异常的辅助函数。"""
        exc = ai_error("AI服务暂时不可用")
        assert isinstance(exc, AIException)
        assert exc.message == "AI服务暂时不可用"
    
    def test_scraper_error_helper(self):
        """测试快速创建爬虫异常的辅助函数。"""
        exc = scraper_error("爬虫任务失败")
        assert isinstance(exc, ScraperException)
        assert exc.message == "爬虫任务失败"
    
    def test_task_error_helper(self):
        """测试快速创建任务异常的辅助函数。"""
        exc = task_error("任务执行超时")
        assert isinstance(exc, TaskException)
        assert exc.message == "任务执行超时"
    
    def test_config_error_helper(self):
        """测试快速创建配置异常的辅助函数。"""
        exc = config_error("配置项缺失")
        assert isinstance(exc, ConfigException)
        assert exc.message == "配置项缺失"


class TestExceptionDetails:
    """测试异常详细信息。"""
    
    def test_exception_with_multiple_details(self):
        """测试带多个详细信息的异常。"""
        details = {
            "error_code": "VALIDATION_ERROR",
            "field": "email",
            "provided_value": "invalid-email",
            "expected_format": "user@example.com"
        }
        exc = ValidationException("email", "邮箱格式不正确", details)
        assert exc.details["error_code"] == "VALIDATION_ERROR"
        assert exc.details["provided_value"] == "invalid-email"
    
    def test_exception_details_immutability(self):
        """测试异常详细信息不可变性。"""
        details = {"code": 404}
        exc = NotFoundException("Resource", "id", details)
        # 修改details不应该影响异常中的details
        details["code"] = 500
        assert exc.details["code"] == 404  # 应该保持原值


class TestExceptionInheritance:
    """测试异常继承关系。"""
    
    def test_all_exceptions_inherit_from_base(self):
        """测试所有异常都继承自基础异常。"""
        exc1 = NotFoundException("Type", "id")
        exc2 = ValidationException("field", "message")
        exc3 = ProcessingException("message")
        
        assert isinstance(exc1, AppException)
        assert isinstance(exc2, AppException)
        assert isinstance(exc3, AppException)
    
    def test_specific_exception_types(self):
        """测试特定异常类型。"""
        assert isinstance(not_found("Type", "id"), NotFoundException)
        assert isinstance(validation_error("field", "msg"), ValidationException)
        assert isinstance(processing_error("msg"), ProcessingException)
        assert isinstance(file_error("msg"), FileException)
        assert isinstance(database_error("msg"), DatabaseException)
        assert isinstance(ai_error("msg"), AIException)
        assert isinstance(scraper_error("msg"), ScraperException)
        assert isinstance(task_error("msg"), TaskException)
        assert isinstance(config_error("msg"), ConfigException)