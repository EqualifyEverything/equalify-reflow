# Redis Configuration

This directory contains Redis configuration for the Equalify Reflow message queue and caching system.

## Files

- `redis.conf` - Redis server configuration optimized for queue operations

## Redis Data Structures

Equalify Reflow uses the following Redis data structures:

### Queues (Lists)

- `eq-pdf:queue:pii` - Queue for PII detection jobs
- `eq-pdf:queue:approval` - Queue for faculty approval workflow
- `eq-pdf:queue:processing` - Queue for AI processing jobs

### Timeout Tracking (Sorted Sets)

- `eq-pdf:timeouts:approval` - Approval timeout tracking (sorted by timestamp)

### Job Data (Hashes)

- `eq-pdf:job:{job_id}` - Individual job metadata and state

### Metrics (Hashes)

- `eq-pdf:metrics:daily` - Daily processing metrics

## Configuration Highlights

### Persistence

- **RDB Snapshots**: Periodic point-in-time snapshots
  - Every 15 minutes if ≥1 key changed
  - Every 5 minutes if ≥10 keys changed
  - Every 1 minute if ≥10,000 keys changed

- **AOF (Append Only File)**: Enabled for better durability
  - Fsync every second (good balance of performance/durability)
  - Auto-rewrite when file grows by 100%

### Memory Management

- Configured via environment variables
- LRU eviction policy for cache-like behavior
- Memory limits set via Docker Compose resource constraints

### Performance

- Optimized for blocking queue operations (BLPOP, BRPOP)
- Active defragmentation enabled
- Fast background saving

## Connecting to Redis

### From Docker containers

```bash
# Using redis-cli in the Redis container
docker exec -it equalify-pdf-redis redis-cli

# From another container in the network
redis-cli -h redis -p 6379
```

### From host machine

```bash
# If port is exposed (6379)
redis-cli -h localhost -p 6379
```

### Python Example

```python
import redis

# Connect to Redis
client = redis.Redis(
    host='localhost',
    port=6379,
    db=0,
    decode_responses=True
)

# Queue operations
client.lpush('eq-pdf:queue:pii', 'job-123')
job_id = client.brpop('eq-pdf:queue:pii', timeout=5)

# Hash operations
client.hset('eq-pdf:job:123', mapping={
    'status': 'processing',
    'created_at': '2025-01-01T00:00:00Z'
})

# Sorted set operations
client.zadd('eq-pdf:timeouts:approval', {'job-123': 1735689600})
```

## Monitoring

### Check Redis status

```bash
# Ping
redis-cli ping

# Get info
redis-cli info

# Monitor commands in real-time
redis-cli monitor

# Check memory usage
redis-cli info memory

# See slow queries
redis-cli slowlog get 10
```

### Key Operations

```bash
# List all keys (avoid in production!)
redis-cli keys 'eq-pdf:*'

# Get queue length
redis-cli llen eq-pdf:queue:pii

# Get job data
redis-cli hgetall eq-pdf:job:123

# Check timeout set
redis-cli zrange eq-pdf:timeouts:approval 0 -1 WITHSCORES
```

## Data Persistence

Redis data is persisted in the Docker volume `equalify-pdf-redis-data`:

- **RDB file**: `/data/dump.rdb`
- **AOF file**: `/data/appendonly.aof`

### Backup

```bash
# Create backup
docker exec equalify-pdf-redis redis-cli BGSAVE

# Copy RDB file
docker cp equalify-pdf-redis:/data/dump.rdb ./backup-$(date +%Y%m%d).rdb
```

### Restore

```bash
# Stop Redis
docker-compose -f docker-compose.yml -f docker-compose.dev.yml stop redis

# Copy backup file
docker cp ./backup.rdb equalify-pdf-redis:/data/dump.rdb

# Start Redis
docker-compose -f docker-compose.yml -f docker-compose.dev.yml start redis
```

## Security

### Development

- No password required (default configuration)
- Accessible only within Docker network

### Production

Uncomment and configure in `redis.conf`:

```conf
# Set strong password
requirepass your-strong-password-here

# Disable dangerous commands
rename-command FLUSHDB ""
rename-command FLUSHALL ""
rename-command KEYS ""
rename-command CONFIG ""
```

Update connection URLs to include password:
```
REDIS_URL=redis://:your-strong-password-here@redis:6379
```

## Troubleshooting

### Cannot connect to Redis

```bash
# Check if Redis is running
docker ps | grep redis

# Check Redis logs
docker logs equalify-pdf-redis

# Test connection
docker exec equalify-pdf-redis redis-cli ping
```

### Memory issues

```bash
# Check memory usage
redis-cli info memory

# Clear all data (development only!)
redis-cli FLUSHALL

# Clear specific database
redis-cli FLUSHDB
```

### Performance issues

```bash
# Check slow queries
redis-cli slowlog get 10

# Monitor operations
redis-cli monitor

# Check if persistence is blocking
redis-cli info persistence
```