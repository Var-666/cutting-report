<script setup lang="ts">
import type { HrPerson, User } from '../types/auth'

import { toError } from '../utils/errors'
import { computed, onMounted, reactive, ref } from 'vue'
import { grantAdminAccess, listAdminAccounts, revokeAdminAccess, searchHrPeople } from '../api/auth.ts'
import { session } from '../state/session'
import { formatChineseDateTime } from '../utils/dateDisplay.ts'
import ManagementPageHero from '../components/ManagementPageHero.vue'
import AppReportTable from '../components/AppReportTable.vue'
import { useDialogFocus } from '../composables/useDialogFocus.ts'

const admins = ref<User[]>([])
const results = ref<HrPerson[]>([])
const keyword = ref('')
const loading = ref(false)
const searching = ref(false)
const error = ref('')
const notice = ref('')
const dialog = reactive({ visible: false, action: 'grant', target: null as (User | HrPerson) | null, password: '', saving: false, error: '' })
const dialogElement = ref<HTMLElement | null>(null)
const dialogOpen = computed(() => dialog.visible)

const adminExternalIds = computed(() => new Set(
  admins.value.filter((item) => item.external_id).map((item) => String(item.external_id)),
))

function formatTime(value: unknown) {
  return formatChineseDateTime(value, '—')
}

async function loadAdmins() {
  loading.value = true
  error.value = ''
  try {
    const data = await listAdminAccounts()
    admins.value = data.items || []
  } catch (caught) {
    const exception = toError(caught)
    error.value = exception.message
  } finally {
    loading.value = false
  }
}

async function search() {
  if (!keyword.value.trim()) return
  searching.value = true
  error.value = ''
  notice.value = ''
  try {
    const data = await searchHrPeople(keyword.value.trim())
    results.value = data.items || []
  } catch (caught) {
    const exception = toError(caught)
    error.value = exception.message
  } finally {
    searching.value = false
  }
}

function openDialog(action: string, target: User | HrPerson) {
  dialog.visible = true
  dialog.action = action
  dialog.target = target
  dialog.password = ''
  dialog.error = ''
}

function closeDialog() {
  if (dialog.saving) return
  dialog.visible = false
  dialog.target = null
  dialog.password = ''
}

useDialogFocus(dialogOpen, dialogElement, closeDialog)

async function confirmPermissionChange() {
  if (!dialog.password || !dialog.target) return
  dialog.saving = true
  dialog.error = ''
  let succeeded = false
  try {
    if (dialog.action === 'grant') {
      if (!dialog.target.external_id) return
      await grantAdminAccess(dialog.target.external_id, dialog.password)
      notice.value = `已授予 ${dialog.target.display_name} 管理员权限`
    } else {
      if (!('user_id' in dialog.target)) return
      await revokeAdminAccess(dialog.target.user_id, dialog.password)
      notice.value = `已撤销 ${dialog.target.display_name} 的管理员权限`
    }
    await loadAdmins()
    succeeded = true
  } catch (caught) {
    const exception = toError(caught)
    dialog.error = exception.message
  } finally {
    dialog.saving = false
  }
  if (succeeded) closeDialog()
}

onMounted(loadAdmins)

const adminTableColumns = [
  { key: 'c0', title: '姓名', width: 96, align: 'left', fixed: 'left' },
  { key: 'c1', title: '公司工号', width: 128, align: 'left' },
  { key: 'c2', title: '身份来源', width: 136, align: 'center' },
  { key: 'c3', title: '授权信息', minWidth: 280, align: 'left' },
  { key: 'c4', title: '操作', width: 112, align: 'center', fixed: 'right', ellipsis: false },
] as const
</script>

<template>
  <div class="admin-accounts-page management-page">
    <ManagementPageHero
      eyebrow="ACCESS CONTROL"
      title="管理员管理"
      description="从 HR 人员库授权管理员，登录继续使用本人公司工号和 HR 原密码。"
      icon="user"
      status-label="HR 身份认证"
      :status-value="`${admins.length} 位管理员`"
      status-note="系统仅保存管理员权限映射"
      tone="violet"
    />

    <p v-if="notice" class="account-notice" role="status">{{ notice }}</p>
    <p v-if="error" class="account-error" role="alert">{{ error }}</p>

    <section class="panel account-panel">
      <div class="panel-title">
        <div><span>01</span><h2>现有管理员</h2></div>
        <button type="button" :disabled="loading" @click="loadAdmins">{{ loading ? '刷新中' : '刷新' }}</button>
      </div>
      <div class="history-wrap account-table-wrap">
        <AppReportTable :columns="adminTableColumns" :items="admins" :row-key="admin => admin.user_id" :paginate="false" :loading="loading" aria-label="现有管理员">
          <template #c0="{ row: admin }"><strong>{{ admin.display_name }}</strong></template>
          <template #c1="{ row: admin }">{{ admin.company_no || admin.username }}</template>
          <template #c2="{ row: admin }"><span class="source-badge" :class="admin.auth_source">{{ admin.auth_source === 'hr_database' ? 'HR 数据库' : '本地应急账号' }}</span></template>
          <template #c3="{ row: admin }">{{ admin.permission_changed_by ? `${admin.permission_changed_by} · ${formatTime(admin.permission_changed_at)}` : formatTime(admin.created_at) }}</template>
          <template #c4="{ row: admin }">
                <button
                  v-if="admin.auth_source === 'hr_database'"
                  type="button"
                  class="danger-link"
                  :disabled="admin.user_id === session.user?.user_id"
                  :title="admin.user_id === session.user?.user_id ? '不能撤销当前登录账号自己的权限' : ''"
                  @click="openDialog('revoke', admin)"
                >撤销权限</button>
                <span v-else class="protected-text">受保护</span>
          </template>
          <template #empty><span role="status">{{ loading ? '正在读取管理员…' : '暂无管理员' }}</span></template>
        </AppReportTable>
      </div>
    </section>

    <section class="panel account-panel">
      <div class="panel-title"><div><span>02</span><h2>从 HR 人员库添加</h2></div><p>按姓名或公司工号搜索</p></div>
      <form class="hr-search" @submit.prevent="search">
        <label><span>HR 人员</span><input v-model.trim="keyword" name="hr_keyword" maxlength="64" autocomplete="off" spellcheck="false" placeholder="例如：张三或 10086…" required /></label>
        <button class="primary" type="submit" :disabled="searching">{{ searching ? '搜索中' : '搜索' }}</button>
      </form>
      <div v-if="results.length" class="search-results">
        <article v-for="person in results" :key="person.external_id">
          <div><strong>{{ person.display_name }}</strong><span>{{ person.company_no }}</span><small>{{ person.group_no || '未设置集团工号' }}</small></div>
          <button
            type="button"
            :disabled="adminExternalIds.has(String(person.external_id))"
            @click="openDialog('grant', person)"
          >{{ adminExternalIds.has(String(person.external_id)) ? '已是管理员' : '授予管理员权限' }}</button>
        </article>
      </div>
      <p v-else-if="!searching && keyword" class="search-hint">没有搜索结果时，请检查姓名或公司工号后重新搜索。</p>
      <p v-else class="search-hint">被授权人员无需设置新密码，仍使用 HR 数据库中的原密码登录。</p>
    </section>

    <div v-if="dialog.visible" class="permission-dialog-backdrop" @click.self="closeDialog">
      <section ref="dialogElement" class="permission-dialog" role="dialog" aria-modal="true" aria-labelledby="permission-dialog-title" tabindex="-1">
        <header><div><small>SECURITY CHECK</small><h2 id="permission-dialog-title">{{ dialog.action === 'grant' ? '确认授予管理员权限' : '确认撤销管理员权限' }}</h2></div><button type="button" aria-label="关闭" :disabled="dialog.saving" @click="closeDialog">×</button></header>
        <p>操作对象：<strong>{{ dialog.target?.display_name }}</strong>（{{ dialog.target?.company_no || (dialog.target && 'username' in dialog.target ? dialog.target.username : undefined) }}）</p>
        <label><span>当前管理员密码</span><input v-model="dialog.password" name="current_admin_password" type="password" maxlength="128" autocomplete="current-password" required data-dialog-initial-focus @keyup.enter="confirmPermissionChange" /><small>用于确认是当前登录管理员本人操作，不会保存该密码。</small></label>
        <p v-if="dialog.error" class="dialog-error" role="alert">{{ dialog.error }}</p>
        <footer><button type="button" :disabled="dialog.saving" @click="closeDialog">取消</button><button class="primary" type="button" :disabled="dialog.saving || !dialog.password" @click="confirmPermissionChange">{{ dialog.saving ? '验证并提交中' : '验证密码并确认' }}</button></footer>
      </section>
    </div>
  </div>
</template>

<style scoped>
.admin-security-badge{min-width:220px}.account-notice,.account-error{margin:0 0 18px;padding:12px 16px;border-left:4px solid rgb(var(--error-color));background:rgb(var(--success-color) / .1);color:rgb(var(--error-color));font-size:12px}.account-error{border-color:rgb(var(--error-color));background:rgb(var(--error-color) / .1);color:rgb(var(--error-color))}.account-panel{padding:0;overflow:hidden}.source-badge{display:inline-flex;padding:4px 8px;border-radius:999px;background:rgb(var(--info-color) / .1);color:rgb(var(--info-color));font-size:var(--font-auxiliary);font-weight:700}.source-badge.local{background:var(--theme-hover-color);color:var(--text-color-2)}.danger-link{min-height:30px;padding:0 8px;color:rgb(var(--error-color));border:0;background:transparent}.protected-text{color:var(--muted);font-size:var(--font-auxiliary)}.hr-search{display:grid;grid-template-columns:minmax(220px,480px) max-content;align-items:end;gap:12px;padding:24px 26px}.hr-search label,.permission-dialog label{display:grid;gap:7px}.hr-search label span,.permission-dialog label span{font-size:var(--font-auxiliary);font-weight:700;color:var(--text-color-2)}.search-results{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;padding:0 26px 24px}.search-results article{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 16px;border:1px solid var(--line);background:rgb(var(--container-bg-color))}.search-results article div{display:grid;grid-template-columns:auto auto;gap:3px 10px}.search-results article strong{font-size:14px}.search-results article span{color:var(--blue);font-size:12px}.search-results article small{grid-column:1/-1;color:var(--muted)}.search-results article button{flex:0 0 auto}.search-hint{margin:0;padding:0 26px 24px;color:var(--muted);font-size:12px}.permission-dialog-backdrop{position:fixed;z-index:100;inset:0;display:grid;place-items:center;padding:20px;background:rgb(var(--inverted-bg-color) / .6)}.permission-dialog{width:min(480px,100%);padding:24px;border:1px solid var(--line);background:rgb(var(--container-bg-color));box-shadow:var(--theme-popover-shadow)}.permission-dialog header{display:flex;align-items:flex-start;justify-content:space-between}.permission-dialog header small{color:var(--cyan);font-size:var(--font-auxiliary);font-weight:800;letter-spacing:1.6px}.permission-dialog h2{margin:5px 0 0;font-size:20px}.permission-dialog header button{min-width:32px;min-height:32px;padding:0;border:0;font-size:24px}.permission-dialog>p{margin:22px 0 16px;color:var(--muted);font-size:13px}.permission-dialog label small{color:var(--muted);line-height:1.6}.dialog-error{margin:12px 0 0!important;padding:10px;background:rgb(var(--error-color) / .1);color:rgb(var(--error-color))!important}.permission-dialog footer{display:flex;justify-content:flex-end;gap:10px;margin-top:22px}
@media(max-width:760px){.search-results{grid-template-columns:1fr}.hr-search{grid-template-columns:1fr}.admin-security-badge{min-width:0}}
</style>
