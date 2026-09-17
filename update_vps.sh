#!/bin/bash
# =============================================================================
# Script de Atualização da VPS - ServiceTool
# =============================================================================
# Uso: chmod +x update_vps.sh && ./update_vps.sh
# =============================================================================

set -e  # Para execução em caso de erro

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configurações
PROJECT_DIR="/opt/site_servicetool"  # Diretório do projeto na VPS
BACKUP_DIR="/var/backups/site_servicetool"
VENV_DIR="$PROJECT_DIR/venv"
SERVICE_NAME="servicetool"  # Nome do serviço systemd
DB_NAME="servicetool_db"    # Ajuste conforme seu banco
DB_USER="servicetool_user"  # Ajuste conforme seu usuário do banco

# =============================================================================
# FUNÇÕES
# =============================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[AVISO]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERRO]${NC} $1"
}

# =============================================================================
# PRÉ-CHECAGENS
# =============================================================================

check_root() {
    if [[ $EUID -ne 0 ]]; then
       log_error "Este script precisa ser executado como root (use sudo)"
       exit 1
    fi
}

check_directories() {
    if [ ! -d "$PROJECT_DIR" ]; then
        log_error "Diretório do projeto não encontrado: $PROJECT_DIR"
        exit 1
    fi
}

# =============================================================================
# BACKUP
# =============================================================================

create_backup() {
    log_info "Criando backup..."
    
    BACKUP_TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    BACKUP_PATH="$BACKUP_DIR/backup_$BACKUP_TIMESTAMP"
    
    mkdir -p "$BACKUP_PATH"
    
    # Backup do código
    tar -czf "$BACKUP_PATH/code.tar.gz" -C "$PROJECT_DIR" . 2>/dev/null || true
    
    # Backup do banco de dados (se PostgreSQL)
    if command -v pg_dump &> /dev/null; then
        pg_dump -U "$DB_USER" "$DB_NAME" > "$BACKUP_PATH/database.sql" 2>/dev/null || \
            log_warn "Não foi possível fazer backup do banco PostgreSQL"
    fi
    
    # Backup do banco de dados (se MySQL/MariaDB)
    if command -v mysqldump &> /dev/null; then
        mysqldump -u "$DB_USER" "$DB_NAME" > "$BACKUP_PATH/database.sql" 2>/dev/null || \
            log_warn "Não foi possível fazer backup do banco MySQL"
    fi
    
    # Backup do .env
    if [ -f "$PROJECT_DIR/.env" ]; then
        cp "$PROJECT_DIR/.env" "$BACKUP_PATH/.env.backup"
    fi
    
    log_success "Backup criado em: $BACKUP_PATH"
}

# =============================================================================
# ATUALIZAÇÃO DO SISTEMA
# =============================================================================

update_system() {
    log_info "Atualizando pacotes do sistema..."
    DEBIAN_FRONTEND=noninteractive apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -o Dpkg::Options::="--force-confold"
    DEBIAN_FRONTEND=noninteractive apt-get autoremove -y
    DEBIAN_FRONTEND=noninteractive apt-get autoclean
    log_success "Sistema atualizado"
}

# =============================================================================
# ATUALIZAÇÃO DO PROJETO
# =============================================================================

update_project() {
    log_info "Atualizando código do projeto..."
    
    cd "$PROJECT_DIR"
    
    # Salvar alterações locais (stash)
    git stash 2>/dev/null || true
    
    # Puxar últimas alterações
    git pull origin main || git pull origin master
    
    # Restaurar alterações locais se necessário
    git stash pop 2>/dev/null || true
    
    log_success "Código atualizado"
}

# =============================================================================
# DEPENDÊNCIAS PYTHON
# =============================================================================

update_dependencies() {
    log_info "Atualizando dependências Python..."
    
    if [ -d "$VENV_DIR" ]; then
        source "$VENV_DIR/bin/activate"
    else
        log_warn "Virtual environment não encontrado em $VENV_DIR"
        log_info "Criando novo virtual environment..."
        python3 -m venv "$VENV_DIR"
        source "$VENV_DIR/bin/activate"
    fi
    
    pip install --upgrade pip
    
    if [ -f "$PROJECT_DIR/requirements.txt" ]; then
        pip install -r "$PROJECT_DIR/requirements.txt" --upgrade
    fi
    
    log_success "Dependências atualizadas"
}

# =============================================================================
# MIGRAÇÕES DO BANCO
# =============================================================================

run_migrations() {
    log_info "Executando migrações do Django..."
    
    cd "$PROJECT_DIR"
    source "$VENV_DIR/bin/activate"
    
    python manage.py makemigrations --noinput
    python manage.py migrate --noinput
    
    log_success "Migrações executadas"
}

# =============================================================================
# COLETAR ARQUIVOS ESTÁTICOS
# =============================================================================

collect_static() {
    log_info "Coletando arquivos estáticos..."
    
    cd "$PROJECT_DIR"
    source "$VENV_DIR/bin/activate"
    
    python manage.py collectstatic --noinput --clear
    
    log_success "Arquivos estáticos coletados"
}

# =============================================================================
# PERMISSÕES
# =============================================================================

fix_permissions() {
    log_info "Ajustando permissões..."
    
    # Ajustar permissões do diretório do projeto
    chown -R www-data:www-data "$PROJECT_DIR"
    
    # Permissões para arquivos de mídia e static
    if [ -d "$PROJECT_DIR/media" ]; then
        chmod -R 755 "$PROJECT_DIR/media"
    fi
    
    if [ -d "$PROJECT_DIR/staticfiles" ]; then
        chmod -R 755 "$PROJECT_DIR/staticfiles"
    fi
    
    log_success "Permissões ajustadas"
}

# =============================================================================
# REINICIAR SERVIÇOS
# =============================================================================

restart_services() {
    log_info "Reiniciando serviços..."
    
    # Reiniciar serviço do Django (Gunicorn/uWSGI)
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        systemctl restart "$SERVICE_NAME"
        log_success "Serviço $SERVICE_NAME reiniciado"
    else
        log_warn "Serviço $SERVICE_NAME não encontrado ou inativo"
    fi
    
    # Reiniciar Nginx
    if systemctl is-active --quiet nginx; then
        systemctl restart nginx
        log_success "Nginx reiniciado"
    fi
    
    # Reiniciar Supervisor (se usar)
    if command -v supervisorctl &> /dev/null; then
        supervisorctl reread
        supervisorctl update
        supervisorctl restart all
        log_success "Supervisor reiniciado"
    fi
}

# =============================================================================
# VERIFICAÇÃO PÓS-ATUALIZAÇÃO
# =============================================================================

health_check() {
    log_info "Verificando saúde do sistema..."
    
    # Verificar se o serviço está rodando
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log_success "Serviço $SERVICE_NAME está ativo"
    else
        log_error "Serviço $SERVICE_NAME não está ativo!"
        systemctl status "$SERVICE_NAME" --no-pager
    fi
    
    # Verificar uso de disco
    DISK_USAGE=$(df -h / | awk 'NR==2 {print $5}' | sed 's/%//')
    if [ "$DISK_USAGE" -gt 90 ]; then
        log_warn "Uso de disco está em ${DISK_USAGE}%!"
    else
        log_info "Uso de disco: ${DISK_USAGE}%"
    fi
    
    # Verificar memória
    MEMORY_USAGE=$(free | grep Mem | awk '{printf "%.0f", $3/$2 * 100.0}')
    log_info "Uso de memória: ${MEMORY_USAGE}%"
    
    # Testar se o site responde
    if command -v curl &> /dev/null; then
        HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost/ 2>/dev/null || echo "000")
        if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "302" ]; then
            log_success "Site respondendo (HTTP $HTTP_STATUS)"
        else
            log_warn "Site retornou HTTP $HTTP_STATUS"
        fi
    fi
}

# =============================================================================
# LIMPEZA
# =============================================================================

cleanup() {
    log_info "Limpando arquivos temporários..."
    
    # Limpar cache do Django
    find "$PROJECT_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find "$PROJECT_DIR" -type f -name "*.pyc" -delete 2>/dev/null || true
    
    # Limpar logs antigos (manter últimos 7 dias)
    if [ -d "$PROJECT_DIR/logs" ]; then
        find "$PROJECT_DIR/logs" -type f -mtime +7 -delete 2>/dev/null || true
    fi
    
    # Limpar backups antigos (manter últimos 10)
    if [ -d "$BACKUP_DIR" ]; then
        ls -t "$BACKUP_DIR" | tail -n +11 | xargs -I {} rm -rf "$BACKUP_DIR/{}" 2>/dev/null || true
    fi
    
    log_success "Limpeza concluída"
}

# =============================================================================
# MENU INTERATIVO
# =============================================================================

show_menu() {
    echo ""
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}  Script de Atualização da VPS${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
    echo "1. Atualização Completa (Recomendado)"
    echo "2. Apenas atualizar código (git pull)"
    echo "3. Apenas reiniciar serviços"
    echo "4. Criar backup manual"
    echo "5. Verificar saúde do sistema"
    echo "6. Limpar arquivos temporários"
    echo "0. Sair"
    echo ""
}

run_full_update() {
    log_info "Iniciando atualização completa..."
    create_backup
    update_system
    update_project
    update_dependencies
    run_migrations
    collect_static
    fix_permissions
    restart_services
    health_check
    cleanup
    log_success "Atualização completa finalizada!"
}

run_quick_update() {
    log_info "Atualização rápida (apenas código)..."
    create_backup
    update_project
    restart_services
    health_check
    log_success "Atualização rápida finalizada!"
}

# =============================================================================
# EXECUÇÃO PRINCIPAL
# =============================================================================

main() {
    check_root
    check_directories
    
    # Se passou argumento, executar direto
    case "${1:-}" in
        --full|-f)
            run_full_update
            exit 0
            ;;
        --quick|-q)
            run_quick_update
            exit 0
            ;;
        --backup|-b)
            create_backup
            exit 0
            ;;
        --health|-h)
            health_check
            exit 0
            ;;
        --help)
            echo "Uso: $0 [OPÇÃO]"
            echo ""
            echo "Opções:"
            echo "  --full,  -f    Atualização completa"
            echo "  --quick, -q    Atualização rápida (apenas código)"
            echo "  --backup, -b   Criar backup"
            echo "  --health, -h   Verificar saúde do sistema"
            echo "  --help         Mostrar esta ajuda"
            echo ""
            echo "Sem opções: menu interativo"
            exit 0
            ;;
    esac
    
    # Menu interativo
    while true; do
        show_menu
        read -p "Escolha uma opção: " choice
        
        case $choice in
            1)
                run_full_update
                read -p "Pressione Enter para continuar..."
                ;;
            2)
                run_quick_update
                read -p "Pressione Enter para continuar..."
                ;;
            3)
                restart_services
                health_check
                read -p "Pressione Enter para continuar..."
                ;;
            4)
                create_backup
                read -p "Pressione Enter para continuar..."
                ;;
            5)
                health_check
                read -p "Pressione Enter para continuar..."
                ;;
            6)
                cleanup
                read -p "Pressione Enter para continuar..."
                ;;
            0)
                log_info "Saindo..."
                exit 0
                ;;
            *)
                log_warn "Opção inválida!"
                ;;
        esac
    done
}

# Executar
main "$@"
