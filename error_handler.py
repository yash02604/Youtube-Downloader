"""
Enhanced error handling system with exponential backoff and recovery mechanisms
"""
import time
import random
import logging
from typing import Callable, Any, Optional
from config import config_manager

logger = logging.getLogger(__name__)


class AdaptiveBackoff:
    """Implements adaptive backoff retry strategy with multiple algorithms"""
    
    def __init__(self, max_retries: int = None, base_delay: float = None, strategy: str = 'exponential'):
        self.max_retries = max_retries or config_manager.get('retry_attempts', 5)
        self.base_delay = base_delay or config_manager.get('retry_delay_base', 2.0)
        self.strategy = strategy  # 'exponential', 'fibonacci', 'linear', 'decorrelated'
        self.retry_count = 0
        self.previous_delay = 0
    
    def should_retry(self) -> bool:
        """Check if another retry should be attempted"""
        return self.retry_count < self.max_retries
    
    def get_delay(self) -> float:
        """Calculate delay based on selected backoff strategy"""
        if self.retry_count == 0:
            return 0
        
        if self.strategy == 'exponential':
            return self._exponential_backoff()
        elif self.strategy == 'fibonacci':
            return self._fibonacci_backoff()
        elif self.strategy == 'linear':
            return self._linear_backoff()
        elif self.strategy == 'decorrelated':
            return self._decorrelated_backoff()
        else:
            return self._exponential_backoff()
    
    def _exponential_backoff(self) -> float:
        """Standard exponential backoff with jitter"""
        delay = self.base_delay * (2 ** (self.retry_count - 1))
        jitter = random.uniform(0, delay * 0.1)
        return delay + jitter
    
    def _fibonacci_backoff(self) -> float:
        """Fibonacci sequence backoff"""
        fib_sequence = [1, 1, 2, 3, 5, 8, 13, 21, 34, 55]
        fib_index = min(self.retry_count - 1, len(fib_sequence) - 1)
        delay = self.base_delay * fib_sequence[fib_index]
        jitter = random.uniform(0, delay * 0.15)
        return delay + jitter
    
    def _linear_backoff(self) -> float:
        """Linear backoff progression"""
        delay = self.base_delay * self.retry_count
        jitter = random.uniform(0, delay * 0.05)
        return delay + jitter
    
    def _decorrelated_backoff(self) -> float:
        """Decorrelated jitter backoff (AWS recommended)"""
        self.previous_delay = random.uniform(self.base_delay, self.previous_delay * 3.0)
        return self.previous_delay
    
    def increment_retry(self):
        """Increment retry counter"""
        self.retry_count += 1
    
    def reset(self):
        """Reset retry counter"""
        self.retry_count = 0


class ErrorHandler:
    """Centralized error handling with recovery strategies and adaptive backoff"""
    
    # Enhanced error categories with specific handling strategies
    ERROR_STRATEGIES = {
        'network': {
            'retry': True,
            'backoff': True,
            'strategy': 'exponential',
            'max_retries': 5,
            'recoverable': True,
            'cleanup_required': False,
            'timeout_extension': 2.0  # Double timeout on retries
        },
        'file_io': {
            'retry': True,
            'backoff': True,
            'strategy': 'linear',
            'max_retries': 3,
            'recoverable': True,
            'cleanup_required': False
        },
        'yt_dlp': {
            'retry': True,
            'backoff': True,
            'strategy': 'fibonacci',
            'max_retries': 3,
            'recoverable': True,
            'cleanup_required': False,
            'alternative_formats': True
        },
        'memory': {
            'retry': False,
            'backoff': False,
            'recoverable': False,
            'cleanup_required': True,
            'immediate_action': 'cleanup_and_notify'
        },
        'validation': {
            'retry': False,
            'backoff': False,
            'recoverable': False,
            'cleanup_required': False
        },
        'rate_limit': {
            'retry': True,
            'backoff': True,
            'strategy': 'exponential',
            'max_retries': 3,
            'recoverable': True,
            'respect_headers': True  # Respect Retry-After headers
        },
        'server_error': {
            'retry': True,
            'backoff': True,
            'strategy': 'decorrelated',
            'max_retries': 4,
            'recoverable': True,
            'transient_only': True  # Only retry 5xx errors
        }
    }
    
    @staticmethod
    def classify_error(error: Exception) -> str:
        """Classify error type for appropriate handling with enhanced detection"""
        error_str = str(error).lower()
        error_type = type(error).__name__
        
        # Network-related errors
        if any(keyword in error_str for keyword in ['timeout', 'connection', 'network', 'dns', 'connect', 'socket']):
            return 'network'
        elif '429' in error_str or 'rate limit' in error_str or 'too many requests' in error_str:
            return 'rate_limit'
        elif any(str(code) in error_str for code in ['500', '502', '503', '504', '5xx']):
            return 'server_error'
        
        # File I/O errors
        elif any(keyword in error_str for keyword in ['permission', 'access', 'file', 'directory', 'io', 'disk']):
            return 'file_io'
        
        # yt-dlp specific errors
        elif 'yt-dlp' in error_str or 'youtube' in error_str or error_type in ['DownloadError', 'ExtractorError']:
            return 'yt_dlp'
        
        # Memory-related errors
        elif any(keyword in error_str for keyword in ['memory', 'oom', 'alloc', 'out of memory']):
            return 'memory'
        
        # Validation errors
        elif any(keyword in error_str for keyword in ['invalid', 'malformed', 'corrupt', 'bad request', '400', '404']):
            return 'validation'
        
        else:
            return 'unknown'
    
    @staticmethod
    def handle_with_retry(func: Callable, *args, **kwargs) -> tuple[Any, Optional[Exception]]:
        """
        Execute function with automatic retry on failure using adaptive backoff
        
        Returns:
            tuple: (result, exception) - One will be None
        """
        error_category = kwargs.pop('_error_category', 'unknown')
        strategy = ErrorHandler.ERROR_STRATEGIES.get(error_category, ErrorHandler.ERROR_STRATEGIES['unknown'])
        
        backoff = AdaptiveBackoff(
            max_retries=strategy.get('max_retries', 3),
            base_delay=config_manager.get('retry_delay_base', 2.0),
            strategy=strategy.get('strategy', 'exponential')
        )
        
        last_exception = None
        
        while backoff.should_retry():
            try:
                result = func(*args, **kwargs)
                backoff.reset()  # Reset on success
                return result, None
                
            except Exception as e:
                last_exception = e
                error_type = ErrorHandler.classify_error(e)
                
                # Log the error
                logger.error("Operation failed (%s): %s", error_type, e)
                
                # Check if this error type should be retried
                error_strategy = ErrorHandler.ERROR_STRATEGIES.get(error_type, strategy)
                if not error_strategy.get('retry', False):
                    break
                
                # Perform cleanup if needed
                if error_strategy.get('cleanup_required', False):
                    ErrorHandler.perform_cleanup()
                
                # Handle rate limiting specifically
                if error_type == 'rate_limit' and error_strategy.get('respect_headers', False):
                    delay = ErrorHandler._parse_retry_after(e)
                    if delay:
                        logger.info("Respecting Retry-After delay of %.2f seconds", delay)
                        time.sleep(delay)
                        backoff.increment_retry()
                        continue
                
                backoff.increment_retry()
                
                if backoff.should_retry():
                    delay = backoff.get_delay()
                    logger.warning(
                        "Retrying in %.2f seconds (attempt %s/%s)",
                        delay, backoff.retry_count, backoff.max_retries,
                    )
                    time.sleep(delay)
                else:
                    logger.error("Maximum retries reached (%s)", backoff.max_retries)
                    break
        
        return None, last_exception
    
    @staticmethod
    def _parse_retry_after(error: Exception) -> Optional[float]:
        """Parse Retry-After header from error if present"""
        error_str = str(error)
        # Look for Retry-After patterns in error message
        import re
        match = re.search(r'Retry-After[\s:]+(\d+)', error_str, re.IGNORECASE)
        if match:
            return float(match.group(1))
        return None
    
    @staticmethod
    def perform_cleanup():
        """Perform memory and resource cleanup"""
        import gc
        try:
            # Force garbage collection
            collected = gc.collect()
            logger.debug("Garbage collected %s objects", collected)
            
            # Clear any cached data that might be taking memory
            # This would be expanded based on application needs
            
        except Exception as e:
            logger.exception("Memory cleanup failed")
    
    @staticmethod
    def safe_execute(func: Callable, error_handler: Callable = None, *args, **kwargs) -> Any:
        """
        Safely execute a function with error handling
        
        Args:
            func: Function to execute
            error_handler: Custom error handler function
            *args, **kwargs: Arguments for the function
        """
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if error_handler:
                return error_handler(e)
            else:
                error_category = ErrorHandler.classify_error(e)
                logger.exception("Unhandled error in %s (%s)", func.__name__, error_category)
                raise


class UserFriendlyError:
    """Provides user-friendly error messages and suggestions"""

    DOWNLOAD_CATEGORY_MESSAGES = {
        "NETWORK_ERROR": ("The server connection failed. Check your connection and try again.", ["Check your internet connection", "Try again later"]),
        "TIMEOUT": ("The server took too long to respond.", ["Check your internet connection", "Try again later"]),
        "TEMPORARY_HTTP_ERROR": ("The server is temporarily unavailable or rate-limiting requests.", ["Wait a few minutes and retry", "Reduce simultaneous downloads"]),
        "VIDEO_UNAVAILABLE": ("This video is unavailable or has been removed.", ["Check the video URL", "Try another video"]),
        "PRIVATE_VIDEO": ("This video is private and cannot be accessed by this download.", ["Choose a public video you can access"]),
        "LOGIN_REQUIRED": ("YouTube requested sign-in verification for this app request.", ["If the video plays in your browser, select that browser in Settings > YouTube browser session and retry", "You can also wait and retry later"]),
        "AGE_RESTRICTED": ("This video has an age restriction that prevents access.", ["Choose content available without account verification"]),
        "FORMAT_UNAVAILABLE": ("The requested quality or format is unavailable for this video.", ["Choose Best Available or another listed quality"]),
        "FFMPEG_MISSING": ("FFmpeg is required for this operation but was not found.", ["Configure a folder containing ffmpeg and ffprobe"]),
        "FFMPEG_ERROR": ("FFmpeg could not process this media.", ["Check the FFmpeg location and try again"]),
        "JS_RUNTIME_ERROR": ("The JavaScript runtime needed to inspect this video could not run.", ["Check the configured Deno path and version"]),
        "POSTPROCESSING_ERROR": ("The media downloaded, but a processing step failed.", ["Check the application log for details", "Retry with optional embedding disabled"]),
        "DISK_FULL": ("There is not enough free space to save this file.", ["Free disk space or choose another destination"]),
        "PERMISSION_ERROR": ("The app cannot write to the selected destination.", ["Choose a folder where you have write access"]),
        "OUTPUT_VALIDATION_ERROR": ("The downloaded file is incomplete or missing required media streams.", ["The file was kept for diagnosis; check the application log"]),
        "CANCELLED": ("The download was cancelled.", []),
        "UNKNOWN_ERROR": ("The download could not be completed.", ["Check the application log for details", "Retry the download"]),
    }
    
    ERROR_MESSAGES = {
        'network_timeout': {
            'user_message': "The connection timed out. This usually happens when the server is slow or unavailable.",
            'suggestions': [
                "Check your internet connection",
                "Try again in a few minutes",
                "Consider reducing the number of concurrent downloads"
            ],
            'technical_details': "Network timeout error"
        },
        'network_dns': {
            'user_message': "Could not resolve the server address. The website might be temporarily unavailable.",
            'suggestions': [
                "Verify the URL is correct",
                "Check your DNS settings",
                "Try using a different DNS server (like Google DNS: 8.8.8.8)"
            ],
            'technical_details': "DNS resolution failed"
        },
        'network_connection': {
            'user_message': "Unable to establish connection to the server.",
            'suggestions': [
                "Check if your firewall is blocking the connection",
                "Verify the server is online",
                "Try disabling VPN or proxy temporarily"
            ],
            'technical_details': "Connection refused or reset"
        },
        'file_permission': {
            'user_message': "Permission denied when trying to save the file.",
            'suggestions': [
                "Choose a different download location",
                "Run the application as administrator",
                "Check if the destination folder has write permissions"
            ],
            'technical_details': "File system permission error"
        },
        'file_disk_full': {
            'user_message': "Not enough disk space available for the download.",
            'suggestions': [
                "Free up disk space",
                "Choose a different drive with more space",
                "Delete unnecessary files"
            ],
            'technical_details': "Insufficient disk space"
        },
        'yt_dlp_unsupported': {
            'user_message': "This website or video format is not supported.",
            'suggestions': [
                "Try a different video quality",
                "Check if the website is supported by yt-dlp",
                "Consider using the website's official download option"
            ],
            'technical_details': "Unsupported site or format"
        },
        'yt_dlp_ffmpeg': {
            'user_message': "Video processing failed. FFmpeg might not be installed or compatible.",
            'suggestions': [
                "Install FFmpeg on your system",
                "Try downloading in a different format",
                "Use audio-only download instead"
            ],
            'technical_details': "FFmpeg processing error"
        },
        'yt_dlp_rate_limit': {
            'user_message': "Too many requests sent to the server. Rate limiting is in effect.",
            'suggestions': [
                "Wait a few minutes before trying again",
                "Reduce the number of concurrent downloads",
                "Try during off-peak hours"
            ],
            'technical_details': "Rate limiting (HTTP 429)"
        },
        'yt_dlp_forbidden': {
            'user_message': "Access to this content is forbidden (HTTP 403). The server is blocking requests.",
            'suggestions': [
                "Try again later when server restrictions may be lifted",
                "Use a different network connection or VPN",
                "Try downloading at a different time of day",
                "Check if the content requires login or subscription"
            ],
            'technical_details': "Access forbidden (HTTP 403)"
        },
        'server_error_5xx': {
            'user_message': "The server encountered an error and couldn't process your request.",
            'suggestions': [
                "Try again in a few minutes",
                "Check if the service is experiencing downtime",
                "Try accessing the content through the website directly"
            ],
            'technical_details': "Server-side error (5xx status code)"
        },
        'memory_oom': {
            'user_message': "The application ran out of memory while processing the download.",
            'suggestions': [
                "Close other applications to free up memory",
                "Reduce the number of concurrent downloads",
                "Restart the application"
            ],
            'technical_details': "Out of memory error"
        }
    }
    
    @staticmethod
    def get_user_friendly_message(error: Exception, error_category: str = None) -> dict:
        """Get user-friendly error message and suggestions"""
        if error_category in UserFriendlyError.DOWNLOAD_CATEGORY_MESSAGES:
            message, suggestions = UserFriendlyError.DOWNLOAD_CATEGORY_MESSAGES[error_category]
            return {
                "user_message": message,
                "suggestions": suggestions,
                "technical_details": str(error),
            }
        if error_category is None:
            error_category = ErrorHandler.classify_error(error)
        
        error_str = str(error).lower()
        
        # Specific error matching
        if 'timeout' in error_str:
            return UserFriendlyError.ERROR_MESSAGES['network_timeout']
        elif 'dns' in error_str or 'resolve' in error_str:
            return UserFriendlyError.ERROR_MESSAGES['network_dns']
        elif 'connection' in error_str or 'refused' in error_str:
            return UserFriendlyError.ERROR_MESSAGES['network_connection']
        elif 'permission' in error_str or 'access denied' in error_str:
            return UserFriendlyError.ERROR_MESSAGES['file_permission']
        elif 'disk space' in error_str or 'no space' in error_str:
            return UserFriendlyError.ERROR_MESSAGES['file_disk_full']
        elif 'ffmpeg' in error_str:
            return UserFriendlyError.ERROR_MESSAGES['yt_dlp_ffmpeg']
        elif '429' in error_str or 'rate limit' in error_str:
            return UserFriendlyError.ERROR_MESSAGES['yt_dlp_rate_limit']
        elif '403' in error_str or 'forbidden' in error_str:
            return UserFriendlyError.ERROR_MESSAGES['yt_dlp_forbidden']
        elif any(code in error_str for code in ['500', '502', '503', '504']):
            return UserFriendlyError.ERROR_MESSAGES['server_error_5xx']
        elif 'memory' in error_str or 'oom' in error_str:
            return UserFriendlyError.ERROR_MESSAGES['memory_oom']
        
        # Keep unknown errors concise; the detailed text belongs in the log.
        return {
            'user_message': "The operation could not be completed.",
            'suggestions': [
                "Check the application log for details",
                "Try the operation again"
            ],
            'technical_details': str(error)
        }
    
    @staticmethod
    def format_error_display(error_info: dict) -> str:
        """Format error information for display to user"""
        message = f"⚠️  {error_info['user_message']}\n\n"
        message += "💡 Suggestions:\n"
        for i, suggestion in enumerate(error_info['suggestions'], 1):
            message += f"  {i}. {suggestion}\n"
        
        return message


# Convenience decorators
def retry_on_failure(error_category: str = 'unknown', max_retries: int = None):
    """Decorator to automatically retry function on failure"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            kwargs['_error_category'] = error_category
            if max_retries:
                kwargs['_max_retries'] = max_retries
            result, exception = ErrorHandler.handle_with_retry(func, *args, **kwargs)
            if exception:
                raise exception
            return result
        return wrapper
    return decorator


def safe_operation(error_handler: Callable = None):
    """Decorator for safe function execution"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            return ErrorHandler.safe_execute(func, error_handler, *args, **kwargs)
        return wrapper
    return decorator
