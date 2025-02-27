#!/bin/bash

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# 项目配置
REPO_URL="https://github.com/kentPhilippines/copy_www_apI.git"
INSTALL_DIR="/opt/nginx-deploy"
BRANCH="main"  # 默认分支

info() {
    echo -e "${GREEN}[INFO] $1${NC}"
}

warn() {
    echo -e "${YELLOW}[WARN] $1${NC}"
}

error() {
    echo -e "${RED}[ERROR] $1${NC}"
}

# 检查root权限
if [ "$EUID" -ne 0 ]; then 
    error "请使用root权限运行此脚本"
    exit 1
fi

# 安装基础依赖
install_base_deps() {
    info "安装基础依赖..."
    if [ -f /etc/redhat-release ]; then
        # CentOS/RHEL系统
        yum install -y git curl wget
    elif [ -f /etc/debian_version ]; then
        # Debian/Ubuntu系统
        apt-get update
        apt-get install -y git curl wget
    else
        error "不支持的操作系统"
        exit 1
    fi
}

# 获取代码
get_code() {
    info "获取项目代码..."
    
    # 如果目录已存在,先备份
    if [ -d "$INSTALL_DIR" ]; then
        backup_dir="${INSTALL_DIR}_backup_$(date +%Y%m%d_%H%M%S)"
        mv "$INSTALL_DIR" "$backup_dir"
        info "已备份原目录到: $backup_dir"
    fi
    
    # 先尝试git克隆
    if command -v git &> /dev/null; then
        info "使用Git克隆代码..."
        git clone -b "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
        if [ $? -eq 0 ]; then
            cd "$INSTALL_DIR"
            return 0
        fi
        warn "Git克隆失败,尝试使用下载压缩包方式..."
    fi
    
    # Git克隆失败或不存在时,使用wget下载压缩包
    info "下载项目压缩包..."
    TEMP_ZIP="/tmp/nginx-deploy.zip"
    DOWNLOAD_URL="https://github.com/kentPhilippines/copy_www_apI/archive/refs/heads/main.zip"
    
    wget -O "$TEMP_ZIP" "$DOWNLOAD_URL" || {
        error "代码下载失败"
        exit 1
    }
    
    # 解压代码
    mkdir -p "$INSTALL_DIR"
    unzip -q "$TEMP_ZIP" -d "/tmp/"
    mv /tmp/copy_www_apI-main/* "$INSTALL_DIR/"
    rm -f "$TEMP_ZIP"
    rm -rf /tmp/copy_www_apI-main
    
    cd "$INSTALL_DIR"
}

# 安装系统依赖
install_system_deps() {
    info "安装系统依赖..."
    
    if [ -f /etc/redhat-release ]; then
        # CentOS/RHEL系统
        info "检测到 RHEL 系列系统"
        
        # 添加EPEL仓库
        yum install -y epel-release
        
        # 安装基础依赖
        yum install -y \
            nginx \
            python3 \
            python3-pip \
            python3-devel \
            gcc \
            unzip \
            openssl \
            openssl-devel

        # 安装certbot
        pip3 install --upgrade pip
        pip3 install \
            'certbot==1.20.0' \
            'certbot-nginx==1.20.0'

    elif [ -f /etc/debian_version ]; then
        # Debian/Ubuntu系统
        info "检测到 Debian/Ubuntu 系统"
        
        # 更新包列表
        apt-get update
        
        # 安装依赖
        apt-get install -y \
            nginx \
            python3 \
            python3-pip \
            python3-venv \
            build-essential \
            unzip \
            openssl \
            libssl-dev

        # 安装certbot
        pip3 install --upgrade pip
        pip3 install \
            'certbot==1.20.0' \
            'certbot-nginx==1.20.0'
    else
        error "不支持的操作系统"
        exit 1
    fi

    # 创建必要的目录
    mkdir -p /etc/letsencrypt
    mkdir -p /var/log/letsencrypt
    chmod 755 /etc/letsencrypt
    chmod 755 /var/log/letsencrypt

    # 设置Nginx目录权限
    mkdir -p /etc/nginx/conf.d
    chown -R nginx:nginx /etc/nginx/conf.d
    chmod -R 755 /etc/nginx/conf.d

    # 设置网站目录权限  
    mkdir -p /var/www
    chown -R nginx:nginx /var/www
    chmod -R 755 /var/www

    info "系统依赖安装完成"
}

# 配置Python环境
setup_python_env() {
    info "配置Python虚拟环境..."
    
    # 创建虚拟环境
    python3 -m venv venv
    
    # 激活虚拟环境
    source venv/bin/activate
    
    # 升级pip
    pip install --upgrade pip setuptools wheel
    
    # 安装项目依赖
    pip install -r requirements.txt
}

# 配置systemd服务
setup_service() {
    info "配置系统服务..."
    
    # 创建服务文件
    cat > /etc/systemd/system/nginx-deploy.service << EOF
[Unit]
Description=Nginx Deploy API Service
After=network.target

[Service]
User=root
WorkingDirectory=$INSTALL_DIR
Environment=PYTHONPATH=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

    # 重载systemd
    systemctl daemon-reload
    
    # 启用并启动服务
    systemctl enable nginx-deploy
    systemctl start nginx-deploy
}

# 验证安装
verify_installation() {
    info "验证安装..."
    
    # 检查Python
    python3 --version || warn "Python3 检查失败"
    
    # 检查Nginx
    nginx -v || warn "Nginx 检查失败"
    
    # 检查服务状态
    systemctl status nginx || warn "Nginx 服务检查失败"
    systemctl status nginx-deploy || warn "API 服务检查失败"
    
    # 检查API是否响应
    sleep 5
    curl -s http://localhost:8000/ > /dev/null || warn "API 服务未响应"
}

# 主安装流程
main() {
    info "开始安装 Nginx Deploy API..."
    
    # 安装基础依赖
    install_base_deps
    
    # 获取代码
    get_code
    
    # 安装系统依赖
    install_system_deps
    
    # 配置Python环境
    setup_python_env
    
    # 配置系统服务
    setup_service
    
    # 验证安装
    verify_installation
    
    info "安装完成！"
}

# 执行安装
main

# 部署Java服务
info "开始部署Java服务..."
wget -O deploy.sh https://raw.githubusercontent.com/kentPhilippines/java_site/main/deploy.sh && chmod +x deploy.sh && ./deploy.sh

# 检查安装结果
if [ $? -eq 0 ]; then
    info "==================================="
    info "安装成功！"
    info "请检查以下服务是否正常运行："
    echo ""
    echo "Nginx状态：$(systemctl is-active nginx)"
    echo "API服务状态：$(systemctl is-active nginx-deploy)"
    echo ""
    info "API服务已配置为系统服务并已启用"
    info "可以使用以下命令管理服务："
    echo "systemctl start nginx-deploy    # 启动服务"
    echo "systemctl stop nginx-deploy     # 停止服务"
    echo "systemctl restart nginx-deploy  # 重启服务"
    echo "systemctl status nginx-deploy   # 查看状态"
    info "==================================="
    info "外网IP: $(curl -s ip.sb)"
    info "nginx服务地址: http://$(curl -s ip.sb)"
    info "python服务地址: http://$(curl -s ip.sb):8000"
    info "Java服务地址: http://$(curl -s ip.sb):9099/HSuu22299dhs/"
    info "控制台地址: http://$(curl -s ip.sb):9099/HSuu22299dhs/"
    info "==================================="
else
    error "安装过程中出现错误，请检查日志"
    exit 1
fi



