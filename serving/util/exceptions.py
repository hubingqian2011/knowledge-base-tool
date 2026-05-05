class BaseException(Exception):
    """基础异常类"""
    def __init__(self, message: str, code: int = 500):
        self.message = message
        self.code = code
        super().__init__(self.message)

class BusinessException(BaseException):
    """业务逻辑异常"""
    def __init__(self, message: str, code: int = 400):
        super().__init__(message, code)

class ValidationException(BaseException):
    """数据验证异常"""
    def __init__(self, message: str, code: int = 422):
        super().__init__(message, code)

class AuthenticationException(BaseException):
    """认证异常"""
    def __init__(self, message: str, code: int = 401):
        super().__init__(message, code)

class AuthorizationException(BaseException):
    """授权异常"""
    def __init__(self, message: str, code: int = 403):
        super().__init__(message, code)

class ResourceNotFoundException(BaseException):
    """资源未找到异常"""
    def __init__(self, message: str, code: int = 404):
        super().__init__(message, code)

class ServerException(BaseException):
    """服务器内部异常"""
    def __init__(self, message: str, code: int = 500):
        super().__init__(message, code)

class AudioProcessingException(BusinessException):
    """音频处理异常"""
    def __init__(self, message: str, code: int = 400):
        super().__init__(message, code)

class StorageServiceError(BusinessException):
    """存储服务异常"""
    def __init__(self, message: str, code: int = 400):
        super().__init__(message, code)

class StorageUploadError(StorageServiceError):
    """存储上传异常"""
    def __init__(self, message: str, code: int = 400):
        super().__init__(message, code)

class StorageDownloadError(StorageServiceError):
    """存储下载异常"""
    def __init__(self, message: str, code: int = 400):
        super().__init__(message, code)

class StorageDeleteError(StorageServiceError):
    """存储删除异常"""
    def __init__(self, message: str, code: int = 400):
        super().__init__(message, code)

class StorageConfigError(StorageServiceError):
    """存储配置异常"""
    def __init__(self, message: str, code: int = 400):
        super().__init__(message, code) 