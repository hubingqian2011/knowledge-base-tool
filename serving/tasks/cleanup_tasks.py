# -*- coding: utf-8 -*-
import asyncio
from util.logging.logger import get_logger
from database.sql.repository.repository_factory import RepositoryFactory

logger = get_logger(__name__)

class CleanupTasks:
    """定期清理任务"""

    def __init__(self, interval_hours: int = 24):
        """初始化清理任务"""
        self.interval_hours = interval_hours
        self.repository_factory = RepositoryFactory.get_instance()
        self._task = None
        self._running = False

    async def cleanup_expired_tokens(self) -> int:
        """清理过期的刷新令牌"""
        try:
            with self.repository_factory.refresh_token_repository as token_repo:
                count = token_repo.cleanup_expired_tokens()
                if count > 0:
                    logger.info(f"清理过期令牌成功: count={count}")
                return count
        except Exception as e:
            logger.error(f"清理过期令牌失败: {str(e)}")
            return 0

    async def cleanup_expired_sessions(self) -> int:
        """清理过期的用户会话"""
        try:
            with self.repository_factory.user_session_repository as session_repo:
                count = session_repo.cleanup_expired_sessions()
                if count > 0:
                    logger.info(f"清理过期会话成功: count={count}")
                return count
        except Exception as e:
            logger.error(f"清理过期会话失败: {str(e)}")
            return 0

    async def run_cleanup_cycle(self):
        """执行一次完整的清理周期"""
        logger.info("开始执行清理任务...")
        try:
            tokens_cleaned = await self.cleanup_expired_tokens()
            sessions_cleaned = await self.cleanup_expired_sessions()
            total = tokens_cleaned + sessions_cleaned
            logger.info(f"清理任务完成: 总计清理 {total} 条记录")
        except Exception as e:
            logger.error(f"清理任务执行异常: {str(e)}")

    async def start_cleanup_scheduler(self):
        """启动定期清理调度器"""
        if self._running:
            logger.warning("清理调度器已在运行")
            return

        self._running = True
        logger.info(f"启动清理调度器: 间隔 {self.interval_hours} 小时")

        while self._running:
            try:
                await self.run_cleanup_cycle()
                sleep_seconds = self.interval_hours * 3600
                logger.debug(f"下次清理将在 {self.interval_hours} 小时后执行")
                await asyncio.sleep(sleep_seconds)
            except asyncio.CancelledError:
                logger.info("清理调度器收到取消信号")
                break
            except Exception as e:
                logger.error(f"清理调度器异常: {str(e)}")
                await asyncio.sleep(3600)

    def stop_cleanup_scheduler(self):
        """停止清理调度器"""
        if self._running:
            self._running = False
            logger.info("清理调度器已停止")

    async def start_in_background(self):
        """在后台启动清理任务"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.start_cleanup_scheduler())
            logger.info("清理任务已在后台启动")
        return self._task
