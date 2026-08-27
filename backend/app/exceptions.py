"""应用异常定义：提供更详细的错误信息和分类。"""

from typing import Any, Optional


class AppException(Exception):
    """基础异常类，所有自定义异常的基类。
    
    注意：避免使用 BaseException 作为类名，因为这是Python内置类。
    """
    
    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        # 浅拷贝：外部后续修改原 dict 不应影响已创建异常携带的详细信息
        self.details = dict(details) if details else {}
    
    def __str__(self) -> str:
        return self.message


class NotFoundException(AppException):
    """资源未找到异常。"""
    
    def __init__(self, resource_type: str, resource_id: str, details: Optional[dict[str, Any]] = None):
        self.resource_type = resource_type
        self.resource_id = resource_id
        message = f"{resource_type} 未找到: {resource_id}"
        super().__init__(message, details)


class ValidationException(AppException):
    """数据验证异常。"""
    
    def __init__(self, field: str, message: str, details: Optional[dict[str, Any]] = None):
        self.field = field
        message = f"字段 {field} 验证失败: {message}"
        super().__init__(message, details)


class AuthenticationException(AppException):
    """认证异常。"""
    
    def __init__(self, message: str = "认证失败", details: Optional[dict[str, Any]] = None):
        super().__init__(message, details)


class AuthorizationException(AppException):
    """授权异常。"""
    
    def __init__(self, message: str = "权限不足", details: Optional[dict[str, Any]] = None):
        super().__init__(message, details)


class ProcessingException(AppException):
    """处理异常（业务逻辑错误）。"""
    
    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message, details)


class FileException(AppException):
    """文件操作异常。"""
    
    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message, details)


class DatabaseException(AppException):
    """数据库操作异常。"""
    
    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message, details)


class AIException(AppException):
    """AI 服务异常。"""
    
    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message, details)


class ScraperException(AppException):
    """爬虫异常。"""
    
    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message, details)


class TaskException(AppException):
    """任务队列异常。"""
    
    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message, details)


class ConfigException(AppException):
    """配置异常。"""
    
    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message, details)


# 快速创建异常的辅助函数
def not_found(resource_type: str, resource_id: str, details: Optional[dict[str, Any]] = None) -> NotFoundException:
    """快速创建资源未找到异常。"""
    return NotFoundException(resource_type, resource_id, details)


def validation_error(field: str, message: str, details: Optional[dict[str, Any]] = None) -> ValidationException:
    """快速创建验证异常。"""
    return ValidationException(field, message, details)


def processing_error(message: str, details: Optional[dict[str, Any]] = None) -> ProcessingException:
    """快速创建处理异常。"""
    return ProcessingException(message, details)


def file_error(message: str, details: Optional[dict[str, Any]] = None) -> FileException:
    """快速创建文件异常。"""
    return FileException(message, details)


def database_error(message: str, details: Optional[dict[str, Any]] = None) -> DatabaseException:
    """快速创建数据库异常。"""
    return DatabaseException(message, details)


def ai_error(message: str, details: Optional[dict[str, Any]] = None) -> AIException:
    """快速创建AI异常。"""
    return AIException(message, details)


def scraper_error(message: str, details: Optional[dict[str, Any]] = None) -> ScraperException:
    """快速创建爬虫异常。"""
    return ScraperException(message, details)


def task_error(message: str, details: Optional[dict[str, Any]] = None) -> TaskException:
    """快速创建任务异常。"""
    return TaskException(message, details)


def config_error(message: str, details: Optional[dict[str, Any]] = None) -> ConfigException:
    """快速创建配置异常。"""
    return ConfigException(message, details)