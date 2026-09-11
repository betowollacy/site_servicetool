from django.contrib import admin

from .models import (
    Api, ApiLog, Currency, Customer, CustomerOrder, CustomPrice, EmailConfig,
    GatewayLog, Invoice, Inventory, InventoryData, MailData,
    Media, OrderInput, Page, PasswordReset, PaymentDeposit, PaymentGateway,
    RemoteServiceInput, RemoteServiceList, ServiceGroup, ServiceInput,
    ServiceList, Slider, Statement, SystemSetting, TempRegister, User,
)


class CustomerAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'email', 'mobile', 'role', 'balance', 'currency', 'api_allow', 'status', 'created_at')
    search_fields = ('name', 'email', 'mobile')
    list_filter = ('role', 'status', 'api_allow')
    fields = ('name', 'email', 'mobile', 'cpf_cnpj', 'password', 'role', 'balance', 'currency',
              'api_allow', 'api_key', 'api_ip', 'status', 'created_at', 'updated_at')
    readonly_fields = ('created_at', 'updated_at')


class ServiceListAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'service_type', 'service_group', 'original_price', 'status', 'sells', 'views')
    search_fields = ('title', 'subtitle')
    list_filter = ('service_type', 'status', 'service_group')


class CustomerOrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'customer', 'service_title', 'service_status', 'service_type', 'service_price', 'created_at')
    search_fields = ('service_title', 'trx_id')


class SliderAdmin(admin.ModelAdmin):
    list_display = ('id', 'img', 'status', 'url')


class PaymentGatewayAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'currency_code', 'status', 'charge')


class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ('key', 'value')
    search_fields = ('key',)


admin.site.register(User, admin.ModelAdmin)
admin.site.register(Customer, CustomerAdmin)
admin.site.register(Currency)
admin.site.register(ServiceGroup)
admin.site.register(ServiceList, ServiceListAdmin)
admin.site.register(ServiceInput)
admin.site.register(CustomPrice)
admin.site.register(TempRegister)
admin.site.register(PasswordReset)
admin.site.register(CustomerOrder, CustomerOrderAdmin)
admin.site.register(OrderInput)
admin.site.register(Statement)
admin.site.register(Invoice)
admin.site.register(SystemSetting, SystemSettingAdmin)
admin.site.register(Slider, SliderAdmin)
admin.site.register(PaymentGateway, PaymentGatewayAdmin)
admin.site.register(Api)
admin.site.register(RemoteServiceList)
admin.site.register(RemoteServiceInput)
admin.site.register(EmailConfig)
admin.site.register(MailData)
admin.site.register(ApiLog)
admin.site.register(GatewayLog)
admin.site.register(PaymentDeposit)
class InventoryDataInline(admin.TabularInline):
    model = InventoryData
    extra = 0
    fields = ('code', 'status', 'order')
    list_select_related = ('order',)


class InventoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'availableCount', 'soldOutCount', 'linked_service')
    search_fields = ('name', 'services__title')
    inlines = [InventoryDataInline]

    def linked_service(self, obj):
        svc = obj.services.first()
        return svc.title if svc else '-'
    linked_service.short_description = 'Serviço'


class InventoryDataAdmin(admin.ModelAdmin):
    list_display = ('id', 'inventory', 'code', 'status', 'order', 'order_customer')
    list_filter = ('status', 'inventory')
    search_fields = ('code', 'inventory__name', 'order__service_title')
    list_select_related = ('inventory', 'order')

    def order_customer(self, obj):
        return obj.order.customer.name if obj.order and obj.order.customer else '-'
    order_customer.short_description = 'Cliente do pedido'


admin.site.register(Inventory, InventoryAdmin)
admin.site.register(InventoryData, InventoryDataAdmin)
admin.site.register(Media)
admin.site.register(Page)
