import os
import unittest
import uuid

from runtime_hardening import RedisProcessQuota


@unittest.skipUnless(os.getenv("REDIS_TEST_URL"), "REDIS_TEST_URL is required for Redis integration tests")
class RedisQuotaIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from redis.asyncio import Redis

        self.redis = Redis.from_url(os.environ["REDIS_TEST_URL"], decode_responses=True)
        self.prefix = f"cr-agent:test:{uuid.uuid4().hex}"

    async def asyncTearDown(self):
        keys = [key async for key in self.redis.scan_iter(match=f"{self.prefix}:*")]
        if keys:
            await self.redis.delete(*keys)
        await self.redis.aclose()

    async def test_two_instances_share_atomic_concurrency_and_rate_limits(self):
        first_instance = RedisProcessQuota(
            self.redis,
            max_concurrent=1,
            requests_per_minute=1,
            lease_seconds=10,
            key_prefix=self.prefix,
        )
        second_instance = RedisProcessQuota(
            self.redis,
            max_concurrent=1,
            requests_per_minute=1,
            lease_seconds=10,
            key_prefix=self.prefix,
        )

        first = await first_instance.try_acquire("203.0.113.8")
        concurrent = await second_instance.try_acquire("203.0.113.9")
        self.assertTrue(first.allowed)
        self.assertEqual(concurrent.reason, "concurrency")

        await second_instance.release(first.lease_id)
        rate_limited = await second_instance.try_acquire("203.0.113.8")
        self.assertEqual(rate_limited.reason, "rate_limit")


if __name__ == "__main__":
    unittest.main()
