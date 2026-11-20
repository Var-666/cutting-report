import type { Ref } from 'vue'
import { onScopeDispose, reactive, ref } from 'vue'
import { getAutomationConfiguration, saveAutomationConfiguration } from '../api/automation'
import { toError } from '../utils/errors'

export function useAutomationConfiguration({ hasUnsavedChanges, refresh }: { hasUnsavedChanges: Ref<boolean>; refresh: () => Promise<void> }) {
  const configurationLoading = ref(false)
  const configurationLoaded = ref(false)
  const configurationSaving = ref(false)
  const configurationMessage = ref('')
  const configurationError = ref('')
  const configuration = reactive({
    automation_enabled: true,
    automation_scan_seconds: 15,
    email_retry_base_seconds: 60,
    smtp_enabled: false,
    smtp_host: '',
    smtp_port: 587,
    smtp_username: '',
    smtp_password: '',
    smtp_password_configured: false,
    smtp_from_email: '',
    smtp_from_name: '切割生产智能产报系统',
    smtp_use_tls: true,
    smtp_use_ssl: false,
    smtp_timeout_seconds: 20,
  })

  let savedConfiguration = { ...configuration }
  let disposed = false
  onScopeDispose(() => { disposed = true }, true)

  function resetConfiguration() {
    Object.assign(configuration, savedConfiguration)
    configurationError.value = ''
    hasUnsavedChanges.value = false
  }

  function selectTls() {
    if (configuration.smtp_use_tls) configuration.smtp_use_ssl = false
  }

  function selectSsl() {
    if (configuration.smtp_use_ssl) configuration.smtp_use_tls = false
  }

  async function loadConfiguration() {
    if (disposed || configurationLoading.value || configurationSaving.value) return
    configurationLoading.value = true
    configurationError.value = ''
    try {
      const data = await getAutomationConfiguration()
      if (disposed) return
      savedConfiguration = { ...configuration, ...data, smtp_password: '' }
      resetConfiguration()
      configurationLoaded.value = true
    } catch (caught) {
      const exception = toError(caught)
      if (!disposed) configurationError.value = exception.message
    } finally {
      if (!disposed) configurationLoading.value = false
    }
  }

  async function submitConfiguration() {
    if (disposed || configurationSaving.value || configurationLoading.value || !configurationLoaded.value) return
    configurationSaving.value = true
    configurationMessage.value = ''
    configurationError.value = ''
    try {
      const result = await saveAutomationConfiguration({
        automation_enabled: configuration.automation_enabled,
        automation_scan_seconds: Number(configuration.automation_scan_seconds),
        email_retry_base_seconds: Number(configuration.email_retry_base_seconds),
        smtp_enabled: configuration.smtp_enabled,
        smtp_host: configuration.smtp_host,
        smtp_port: Number(configuration.smtp_port),
        smtp_username: configuration.smtp_username,
        smtp_password: configuration.smtp_password || null,
        smtp_from_email: configuration.smtp_from_email,
        smtp_from_name: configuration.smtp_from_name,
        smtp_use_tls: configuration.smtp_use_tls,
        smtp_use_ssl: configuration.smtp_use_ssl,
        smtp_timeout_seconds: Number(configuration.smtp_timeout_seconds),
      })
      if (disposed) return
      if (configuration.smtp_password) configuration.smtp_password_configured = true
      configuration.smtp_password = ''
      savedConfiguration = { ...configuration }
      configurationMessage.value = result.message
      hasUnsavedChanges.value = false
      await refresh()
    } catch (caught) {
      const exception = toError(caught)
      if (!disposed) configurationError.value = exception.message
    } finally {
      if (!disposed) configurationSaving.value = false
    }
  }

  return { configurationLoading, configurationLoaded, configurationSaving, configurationMessage, configurationError, configuration, selectTls, selectSsl, loadConfiguration, submitConfiguration, resetConfiguration }
}
