"""
Memory management and optimization system
Handles resource cleanup, garbage collection, and memory monitoring
"""
import gc
import logging
import psutil
import threading
import time
from typing import Dict, List, Callable, Optional
from collections import defaultdict, deque
from config import config_manager

logger = logging.getLogger(__name__)


class MemoryManager:
    """Manages memory usage and performs periodic cleanup with smart monitoring"""
    
    def __init__(self):
        self.cleanup_callbacks: List[Callable] = []
        self.memory_stats = defaultdict(deque)
        self.max_stats_history = 100
        self.cleanup_interval = config_manager.get('memory_cleanup_interval', 300)
        self.monitoring_active = False
        self.monitor_thread = None
        
        # Smart monitoring thresholds
        self.warning_threshold = 75.0  # Percent memory usage for warning
        self.critical_threshold = 85.0  # Percent memory usage for immediate cleanup
        self.leak_detection_threshold = 100 * 1024 * 1024  # 100MB increase triggers leak check
        self.last_warning_time = 0
        self.warning_cooldown = 60  # Seconds between warnings
        
        # Memory leak detection
        self.baseline_memory = 0
        self.leak_check_interval = 300  # Check for leaks every 5 minutes
        self.leak_check_counter = 0
        
    def register_cleanup_callback(self, callback: Callable):
        """Register a function to be called during memory cleanup"""
        self.cleanup_callbacks.append(callback)
    
    def unregister_cleanup_callback(self, callback: Callable):
        """Remove a cleanup callback"""
        if callback in self.cleanup_callbacks:
            self.cleanup_callbacks.remove(callback)
    
    def get_memory_usage(self) -> Dict[str, float]:
        """Get current memory usage statistics"""
        try:
            process = psutil.Process()
            memory_info = process.memory_info()
            system_memory = psutil.virtual_memory()
            
            return {
                'rss': memory_info.rss / 1024 / 1024,  # MB
                'vms': memory_info.vms / 1024 / 1024,  # MB
                'percent': process.memory_percent(),
                'system_available': system_memory.available / 1024 / 1024,  # MB
                'system_total': system_memory.total / 1024 / 1024,  # MB
                'system_percent': system_memory.percent,
                'system_used': system_memory.used / 1024 / 1024,  # MB
            }
        except Exception as e:
            logger.warning("Could not read memory stats: %s", e)
            return {}
    
    def log_memory_stats(self) -> Dict[str, float]:
        """Log current memory statistics and return stats"""
        stats = self.get_memory_usage()
        if stats:
            timestamp = time.time()
            for key, value in stats.items():
                self.memory_stats[key].append((timestamp, value))
                # Keep only recent history
                if len(self.memory_stats[key]) > self.max_stats_history:
                    self.memory_stats[key].popleft()
            
            logger.info(
                "Memory usage RSS=%.1fMB VMS=%.1fMB process=%.1f%% system=%.1f%%",
                stats['rss'], stats['vms'], stats['percent'], stats['system_percent'],
            )
        return stats
    
    def perform_cleanup(self, reason: str = "manual") -> int:
        """Perform comprehensive memory cleanup"""
        logger.info("Performing memory cleanup (%s)", reason)
        
        objects_collected = 0
        
        # Run registered cleanup callbacks
        for callback in self.cleanup_callbacks:
            try:
                callback()
            except Exception:
                logger.exception("Memory cleanup callback failed")
        
        # Force garbage collection
        gc.collect()
        
        # Run garbage collection multiple times for thorough cleanup
        for _ in range(3):
            collected = gc.collect()
            objects_collected += collected
            if collected == 0:
                break
        
        # Clear caches and temporary data
        self._clear_caches()
        
        # Update baseline after cleanup
        self._update_baseline()
        
        logger.info("Memory cleanup completed; collected %s objects", objects_collected)
        self.log_memory_stats()
        
        return objects_collected
    
    def _clear_caches(self):
        """Clear application caches and temporary data"""
        # Clear download manager temporary data
        try:
            # This would connect to download manager to clear temp files
            # For now, we'll just demonstrate the concept
            pass
        except Exception as e:
            logger.warning("Cache clearing failed: %s", e)
    
    def _update_baseline(self):
        """Update memory baseline for leak detection"""
        stats = self.get_memory_usage()
        if stats:
            self.baseline_memory = stats['rss']
    
    def start_monitoring(self):
        """Start periodic memory monitoring"""
        if self.monitoring_active:
            return
        
        self.monitoring_active = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        logger.info("Memory monitoring started")
    
    def stop_monitoring(self):
        """Stop memory monitoring"""
        self.monitoring_active = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        logger.info("Memory monitoring stopped")
    
    def _monitor_loop(self):
        """Background monitoring loop with smart thresholds"""
        while self.monitoring_active:
            try:
                stats = self.log_memory_stats()
                
                if stats:
                    # Check for critical memory usage
                    if stats.get('percent', 0) > self.critical_threshold:
                        if self._should_warn():
                            logger.critical(
                                "High memory usage (%.1f%%); performing emergency cleanup",
                                stats['percent'],
                            )
                        self.perform_cleanup("critical")
                    
                    # Check for warning level memory usage
                    elif stats.get('percent', 0) > self.warning_threshold:
                        if self._should_warn():
                            logger.warning("Memory usage is high (%.1f%%)", stats['percent'])
                    
                    # Check for memory leaks periodically
                    self.leak_check_counter += 1
                    if self.leak_check_counter >= self.leak_check_interval:
                        self._check_for_leaks(stats)
                        self.leak_check_counter = 0
                
                # Wait for next interval
                for _ in range(int(self.cleanup_interval)):
                    if not self.monitoring_active:
                        break
                    time.sleep(1)
                    
            except Exception as e:
                logger.exception("Memory monitoring failed: %s", e)
                time.sleep(60)  # Wait longer on error
    
    def get_stats_history(self, stat_name: str) -> List[tuple]:
        """Get historical data for a specific statistic"""
        return list(self.memory_stats.get(stat_name, []))
    
    def _should_warn(self) -> bool:
        """Check if we should send a warning (respect cooldown period)"""
        current_time = time.time()
        if current_time - self.last_warning_time > self.warning_cooldown:
            self.last_warning_time = current_time
            return True
        return False
    
    def _check_for_leaks(self, current_stats: Dict[str, float]):
        """Check for potential memory leaks"""
        if self.baseline_memory == 0:
            self._update_baseline()
            return
        
        current_rss = current_stats.get('rss', 0)
        memory_increase = current_rss - self.baseline_memory
        
        if memory_increase > self.leak_detection_threshold:
            logger.warning("Potential memory leak: increased by %.1fMB", memory_increase)
            # Trigger aggressive cleanup
            self.perform_cleanup("leak_detection")
            # Update baseline after cleanup
            self._update_baseline()
    
    def get_memory_alerts(self) -> List[str]:
        """Get current memory alerts based on thresholds"""
        alerts = []
        stats = self.get_memory_usage()
        
        if stats:
            if stats.get('percent', 0) > self.critical_threshold:
                alerts.append(f"CRITICAL: Process memory usage is {stats['percent']:.1f}%")
            elif stats.get('percent', 0) > self.warning_threshold:
                alerts.append(f"WARNING: Process memory usage is {stats['percent']:.1f}%")
            
            if stats.get('system_percent', 0) > 90:
                alerts.append(f"SYSTEM WARNING: System memory usage is {stats['system_percent']:.1f}%")
        
        return alerts


class ResourceTracker:
    """Tracks resource usage of specific operations"""
    
    def __init__(self):
        self.resources = defaultdict(dict)
    
    def track_resource(self, resource_id: str, **kwargs):
        """Track resource usage for an operation"""
        self.resources[resource_id].update({
            'start_time': time.time(),
            'start_memory': self._get_current_memory(),
            **kwargs
        })
    
    def finish_tracking(self, resource_id: str) -> Dict[str, float]:
        """Finish tracking and return resource usage statistics"""
        if resource_id not in self.resources:
            return {}
        
        start_data = self.resources[resource_id]
        end_time = time.time()
        end_memory = self._get_current_memory()
        
        duration = end_time - start_data['start_time']
        memory_used = end_memory - start_data['start_memory']
        
        stats = {
            'duration': duration,
            'memory_used_mb': memory_used,
            **{k: v for k, v in start_data.items() if k not in ['start_time', 'start_memory']}
        }
        
        # Clean up tracking data
        del self.resources[resource_id]
        
        return stats
    
    def _get_current_memory(self) -> float:
        """Get current memory usage in MB"""
        try:
            return psutil.Process().memory_info().rss / 1024 / 1024
        except Exception:
            return 0.0


# Global instances
memory_manager = MemoryManager()
resource_tracker = ResourceTracker()


def initialize_memory_management():
    """Initialize memory management system"""
    # Register cleanup callbacks
    # memory_manager.register_cleanup_callback(some_cleanup_function)
    
    # Start monitoring if configured
    if config_manager.get('memory_monitoring_enabled', True):
        memory_manager.start_monitoring()


def cleanup_resources():
    """Perform final cleanup before application exit"""
    memory_manager.stop_monitoring()
    memory_manager.perform_cleanup()
