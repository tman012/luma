# Luma Deployment Notes

## Docker Volumes

### Hero Cache Images (`hero_cache` volume)

**What**: Cached hero section backdrop and poster images  
**Path**: `/app/static/hero_cache` (inside container)  
**Volume**: Named volume `hero_cache` in `docker-compose.yml`

**Why persistent**: Hero images are downloaded from Plex/Fanart/TMDb and take time to fetch. The named volume ensures these images survive:
- Container restarts
- Image rebuilds
- Stack redeployments

**Self-healing**: If image files are missing (e.g., after manual volume deletion), the app automatically re-downloads them on next request.

**Served path**: Images are served via Flask at `/static/hero_cache/{filename}`.

### First Deploy

When deploying for the first time:

```bash
docker compose up -d --build
```

The `hero_cache` volume will be automatically created and persist across future deployments.

### Volume Management

**List volumes**:
```bash
docker volume ls | grep hero_cache
```

**Inspect volume**:
```bash
docker volume inspect luma_hero_cache
```

**Clear cache (if needed)**:
```bash
docker compose down
docker volume rm luma_hero_cache
docker compose up -d
```

The app will re-download images on demand.

### Backup

To backup hero cache images:
```bash
docker run --rm -v luma_hero_cache:/data -v $(pwd):/backup alpine tar czf /backup/hero_cache_backup.tar.gz -C /data .
```

To restore:
```bash
docker run --rm -v luma_hero_cache:/data -v $(pwd):/backup alpine sh -c "cd /data && tar xzf /backup/hero_cache_backup.tar.gz"
```

## Data Directory

The main data directory (`${LUMA_DATA_DIR}`) contains:
- `media.db` - Main SQLite database
- `settings.json` - Application settings
- `hero_content.json` - Hero section content metadata
- Cache databases (explore, library)

This should be backed up regularly as it contains all app state except the hero images.
