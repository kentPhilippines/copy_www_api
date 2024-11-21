import os
import aiofiles
from typing import List, Optional
from app.schemas.nginx import NginxSite, NginxStatus, NginxResponse
from app.utils.shell import run_command
from app.utils.nginx import (
    generate_nginx_config,
    get_nginx_config_path,
    get_nginx_enabled_path,
    create_nginx_directories,
    get_site_root_path,
    get_nginx_user
)
from app.core.logger import setup_logger

logger = setup_logger(__name__)

class NginxService:
    """Nginx服务管理"""

    async def restart_nginx(self):
        """重启Nginx服务"""
        try:
            # 先测试配置
            await run_command("nginx -t")
            # 重启服务
            await run_command("systemctl restart nginx")
            logger.info("Nginx服务重启成功")
        except Exception as e:
            logger.error(f"Nginx服务重启失败: {str(e)}")
            raise

    async def _get_system_nginx_user(self) -> str:
        """获取系统Nginx用户"""
        try:
            # 首先检查现有的nginx进程
            ps_result = await run_command("ps aux | grep nginx | grep -v grep | head -n 1 | awk '{print $1}'")
            if ps_result.strip():
                return f"{ps_result.strip()}:{ps_result.strip()}"

            # 检查系统类型
            if os.path.exists('/etc/redhat-release'):
                # CentOS/RHEL系统通常使用nginx:nginx
                # 检查用户是否存在
                try:
                    await run_command("id nginx")
                    return "nginx:nginx"
                except:
                    # 如果nginx用户不存在，使用nobody
                    return "nobody:nobody"
            else:
                # Debian/Ubuntu系统通常使用www-data
                try:
                    await run_command("id www-data")
                    return "www-data:www-data"
                except:
                    # 如果www-data用户不存在，使用nobody
                    return "nobody:nobody"
        except:
            # 默认返回nobody
            return "nobody:nobody"

    async def _ensure_nginx_user(self) -> str:
        """确保Nginx用户存在"""
        try:
            nginx_user = await self._get_system_nginx_user()
            user = nginx_user.split(':')[0]

            # 如果用户不是nobody，确保用户存在
            if user != "nobody":
                try:
                    # 检查用户是否存在
                    await run_command(f"id {user}")
                except:
                    # 用户不存在，创建用户
                    if os.path.exists('/etc/redhat-release'):
                        await run_command(f"useradd -r -s /sbin/nologin {user}")
                    else:
                        await run_command(f"useradd -r -s /usr/sbin/nologin {user}")
                    logger.info(f"创建Nginx用户: {user}")

            return nginx_user
        except Exception as e:
            logger.error(f"确保Nginx用户存在失败: {str(e)}")
            return "nobody:nobody"

    async def _init_nginx_config(self):
        """初始化Nginx配置"""
        try:
            # 确保Nginx用户存在
            nginx_user = await self._ensure_nginx_user()
            logger.info(f"使用Nginx用户: {nginx_user}")
            
            # 创建必要的目录结构
            dirs = [
                "/etc/nginx/sites-available",
                "/etc/nginx/sites-enabled",
                "/var/www",
                "/var/log/nginx",
                "/etc/nginx/conf.d"
            ]
            for dir_path in dirs:
                os.makedirs(dir_path, exist_ok=True)
                await run_command(f"chown -R {nginx_user} {dir_path}")
                await run_command(f"chmod -R 755 {dir_path}")

            # 创建主配置文件
            main_config = f"""
user {nginx_user};
worker_processes auto;
pid /run/nginx.pid;

events {{
    worker_connections 1024;
    multi_accept on;
    use epoll;
}}

http {{
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    server_tokens off;

    # MIME
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    # 日志格式
    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for"';

    # 日志配置
    access_log /var/log/nginx/access.log main;
    error_log /var/log/nginx/error.log warn;

    # Gzip压缩
    gzip on;
    gzip_disable "msie6";
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml application/xml+rss text/javascript;

    # 虚拟主机配置
    include /etc/nginx/conf.d/*.conf;
    include /etc/nginx/sites-enabled/*;
}}
"""
            # 写入主配置文件
            async with aiofiles.open("/etc/nginx/nginx.conf", 'w') as f:
                await f.write(main_config)

            # 设置配置文件权限
            await run_command(f"chown {nginx_user} /etc/nginx/nginx.conf")
            await run_command("chmod 644 /etc/nginx/nginx.conf")

            # 禁用默认站点
            default_site = "/etc/nginx/sites-enabled/default"
            if os.path.exists(default_site):
                os.remove(default_site)
                logger.info("已禁用默认站点")

            # 创建默认的 conf.d 配置
            default_conf = """
# 默认配置
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    return 444;
}
"""
            default_conf_path = "/etc/nginx/conf.d/default.conf"
            async with aiofiles.open(default_conf_path, 'w') as f:
                await f.write(default_conf)

            await run_command(f"chown {nginx_user} {default_conf_path}")
            await run_command("chmod 644 {default_conf_path}")

            logger.info("Nginx配置初始化完成")

        except Exception as e:
            logger.error(f"初始化Nginx配置失败: {str(e)}")
            raise

    async def verify_site_access(self, domain: str) -> bool:
        """验证站点是否可以访问"""
        try:
            # 使用curl检查站点访问
            result = await run_command(f"curl -s -I http://{domain}")
            if "200 OK" in result:
                # 获取页面内容
                content = await run_command(f"curl -s http://{domain}")
                if "部署状态" in content:
                    logger.info(f"站点 {domain} 可以正常访问测试页面")
                    return True
                else:
                    logger.warning(f"站点 {domain} 返回了非预期的内容")
            else:
                logger.warning(f"站点 {domain} 返回了非200状态码")
            return False
        except Exception as e:
            logger.error(f"验证站点访问失败: {str(e)}")
            return False

    async def create_site(self, site: NginxSite) -> NginxResponse:
        """创建站点配置"""
        try:
            # 初始化Nginx配置
            await self._init_nginx_config()
            
            # 创建必要的目录
            create_nginx_directories()
            
            # 获取正确的Nginx用户
            nginx_user = await get_nginx_user()
            
            # 确保站点目录存在并设置权限
            site_root = get_site_root_path(site.domain)
            os.makedirs(site_root, exist_ok=True)
            
            # 设置目录权限
            await run_command(f"chown -R {nginx_user} {site_root}")
            await run_command(f"chmod -R 755 {site_root}")
            
            # 确保日志目录存在
            log_dir = "/var/log/nginx"
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
            await run_command(f"chown -R {nginx_user} {log_dir}")
            
            # 生成配置文件
            config_content = generate_nginx_config(site)
            config_path = get_nginx_config_path(site.domain)
            
            # 确保配置目录存在
            config_dir = os.path.dirname(config_path)
            os.makedirs(config_dir, exist_ok=True)
            
            # 写入配置文件
            async with aiofiles.open(config_path, 'w') as f:
                await f.write(config_content)
            
            # 设置配置文件权限
            await run_command(f"chown {nginx_user} {config_path}")
            await run_command(f"chmod 644 {config_path}")
            
            # 创建软链接
            enabled_path = get_nginx_enabled_path(site.domain)
            enabled_dir = os.path.dirname(enabled_path)
            os.makedirs(enabled_dir, exist_ok=True)
            
            if os.path.exists(enabled_path):
                os.remove(enabled_path)
            os.symlink(config_path, enabled_path)
            
            # 测试配置
            try:
                await run_command("nginx -t")
            except Exception as e:
                logger.error(f"Nginx配置测试失败: {str(e)}")
                # 如果配置测试失败，删除配置文件和软链接
                if os.path.exists(config_path):
                    os.remove(config_path)
                if os.path.exists(enabled_path):
                    os.remove(enabled_path)
                raise
            
            # 重启Nginx
            await self.restart_nginx()
            
            # 等待几秒让服务完全启动
            await run_command("sleep 3")
            
            # 验证站点访问
            if not await self.verify_site_access(site.domain):
                logger.error(f"站点 {site.domain} 无法访问，尝试重新加载配置")
                # 尝试重新加载配置
                await run_command("nginx -s reload")
                await run_command("sleep 2")
                
                # 再次验证
                if not await self.verify_site_access(site.domain):
                    raise Exception(f"站点 {site.domain} 部署后无法访问")
            
            return NginxResponse(
                success=True,
                message=f"站点 {site.domain} 创建成功"
            )
            
        except Exception as e:
            logger.error(f"创建站点失败: {str(e)}")
            raise

    async def delete_site(self, domain: str) -> NginxResponse:
        """删除站点配置"""
        try:
            # 先停止Nginx服务
            await run_command("systemctl stop nginx")
            logger.info("Nginx服务已停止")

            # 删除所有相关文件和目录
            paths_to_delete = [
                # Nginx配置文件
                f"/etc/nginx/sites-available/{domain}.conf",
                f"/etc/nginx/sites-enabled/{domain}.conf",
                # 站点目录
                f"/var/www/{domain}",
                # 日志文件
                f"/var/log/nginx/{domain}.access.log",
                f"/var/log/nginx/{domain}.error.log",
                # 可能的SSL证书目录
                f"/etc/letsencrypt/live/{domain}",
                f"/etc/letsencrypt/archive/{domain}",
                f"/etc/letsencrypt/renewal/{domain}.conf"
            ]

            for path in paths_to_delete:
                if os.path.exists(path):
                    if os.path.isdir(path):
                        await run_command(f"rm -rf {path}")
                    else:
                        os.remove(path)
                    logger.info(f"删除: {path}")

            # 清理Nginx缓存
            cache_dirs = [
                "/var/cache/nginx",
                "/var/tmp/nginx",
                "/run/nginx"  # 添加运行时目录
            ]

            for cache_dir in cache_dirs:
                if os.path.exists(cache_dir):
                    await run_command(f"rm -rf {cache_dir}/*")
                    logger.info(f"清理缓存目录: {cache_dir}")
                    # 重新创建目录
                    os.makedirs(cache_dir, exist_ok=True)

            # 重新设置缓存目录权限
            nginx_user = await get_nginx_user()
            for cache_dir in cache_dirs:
                if os.path.exists(cache_dir):
                    await run_command(f"chown -R {nginx_user} {cache_dir}")
                    await run_command(f"chmod -R 755 {cache_dir}")

            # 检查并删除可能存在的其他配置引用
            nginx_conf = "/etc/nginx/nginx.conf"
            if os.path.exists(nginx_conf):
                # 创建备份
                await run_command(f"cp {nginx_conf} {nginx_conf}.bak")
                # 删除包含该域名的行
                await run_command(f"sed -i '/{domain}/d' {nginx_conf}")

            # 重新启动Nginx前，测试配置
            try:
                await run_command("nginx -t")
            except Exception as e:
                logger.error(f"Nginx配置测试失败: {str(e)}")
                # 如果测试失败，恢复备份
                if os.path.exists(f"{nginx_conf}.bak"):
                    await run_command(f"mv {nginx_conf}.bak {nginx_conf}")
                raise

            # 完全停止并重启Nginx
            await run_command("systemctl stop nginx")
            await run_command("sleep 2")  # 等待服务完全停止
            await run_command("systemctl start nginx")
            await run_command("sleep 2")  # 等待服务完全启动
            logger.info("Nginx服务已重启")

            # 验证域名是否确实无法访问
            max_retries = 3
            retry_count = 0
            while retry_count < max_retries:
                try:
                    result = await run_command(f"curl -s -I --connect-timeout 5 http://{domain} || true")
                    if "200 OK" in result or "301 Moved Permanently" in result:
                        logger.warning(f"警告：域名 {domain} 仍然可以访问，尝试强制重载配置")
                        # 强制重载配置
                        await run_command("systemctl stop nginx")
                        await run_command("sleep 2")
                        await run_command("systemctl start nginx")
                        await run_command("sleep 2")
                        retry_count += 1
                    else:
                        logger.info(f"域名 {domain} 已无法访问")
                        break
                except Exception:
                    logger.info(f"域名 {domain} 已无法访问")
                    break

                if retry_count == max_retries:
                    raise Exception(f"无法完全删除站点 {domain} 的访问")

            return NginxResponse(
                success=True,
                message=f"站点 {domain} 删除成功"
            )
            
        except Exception as e:
            logger.error(f"删除站点失败: {str(e)}")
            # 确保Nginx在发生错误时也能重新启动
            try:
                await run_command("systemctl start nginx")
            except:
                pass
            raise

    async def reload(self) -> NginxResponse:
        """重新加载Nginx配置"""
        try:
            await run_command("nginx -s reload")
            return NginxResponse(
                success=True,
                message="Nginx配置重新加载成功"
            )
        except Exception as e:
            logger.error(f"重新加载Nginx配置失败: {str(e)}")
            raise 