<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import {
  grantAdminAccess,
  listAdminAccounts,
  revokeAdminAccess,
  searchHrPeople,
} from '../api/reports'
import { session } from '../state/session'

const admins = ref([])
const results = ref([])
const keyword = ref('')
const loading = ref(false)
const searching = ref(false)
const error = ref('')
const notice = ref('')
const dialog = reactive({ visible: false, action: 'grant', target: null, password: '', saving: false, error: '' })

const adminExternalIds = computed(() => new Set(
  admins.value.filter((item) => item.external_id).map((item) => String(item.external_id)),
))

function formatTime(value) {
  if (!value) return '—'
  return String(value).replace('T', ' ').slice(0, 19)
}

async function loadAdmins() {
  loading.value = true
  error.value = ''
  try {
    const data = await listAdminAccounts()
    admins.value = data.items || []
  } catch (exception) {
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
  } catch (exception) {
    error.value = exception.message
  } finally {
    searching.value = false
  }
}

function openDialog(action, target) {
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

async function confirmPermissionChange() {
  if (!dialog.password) return
  dialog.saving = true
  dialog.error = ''
  let succeeded = false
  try {
    if (dialog.action === 'grant') {
      await grantAdminAccess(dialog.target.external_id, dialog.password)
      notice.value = `已授予 ${dialog.target.display_name} 管理员权限`
    } else {
      await revokeAdminAccess(dialog.target.user_id, dialog.password)
      notice.value = `已撤销 ${dialog.target.display_name} 的管理员权限`
    }
    await loadAdmins()
    succeeded = true
  } catch (exception) {
    dialog.error = exception.message
  } finally {
    dialog.saving = false
  }
  if (succeeded) closeDialog()
}

onMounted(loadAdmins)
</script>

<template>
  <div class="admin-accounts-page">
    <section class="database-hero admin-accounts-hero">
      <div>
        <span class="eyebrow">ACCESS CONTROL</span>
        <h2>管理员管理</h2>
        <p>从 HR 人员库授权管理员，登录继续使用本人公司工号和 HR 原密码。</p>
      </div>
      <div class="security-badge admin-security-badge">
        <strong>HR 身份认证</strong>
        <span>仅保存管理员权限映射</span>
      </div>
    </section>

    <p v-if="notice" class="account-notice" role="status">{{ notice }}</p>
    <p v-if="error" class="account-error" role="alert">{{ error }}</p>

    <section class="panel account-panel">
      <div class="panel-title">
        <div><span>01</span><h2>现有管理员</h2></div>
        <button type="button" :disabled="loading" @click="loadAdmins">{{ loading ? '刷新中' : '刷新' }}</button>
      </div>
      <div class="history-wrap account-table-wrap">
        <table v-content-table class="history-table account-table">
          <thead><tr><th>姓名</th><th>公司工号</th><th>身份来源</th><th>授权信息</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="admin in admins" :key="admin.user_id">
              <td><strong>{{ admin.display_name }}</strong></td>
              <td>{{ admin.company_no || admin.username }}</td>
              <td><span class="source-badge" :class="admin.auth_source">{{ admin.auth_source === 'hr_database' ? 'HR 数据库' : '本地应急账号' }}</span></td>
              <td>{{ admin.permission_changed_by ? `${admin.permission_changed_by} · ${formatTime(admin.permission_changed_at)}` : formatTime(admin.created_at) }}</td>
              <td>
                <button
                  v-if="admin.auth_source === 'hr_database'"
                  type="button"
                  class="danger-link"
                  :disabled="admin.user_id === session.user?.user_id"
                  :title="admin.user_id === session.user?.user_id ? '不能撤销当前登录账号自己的权限' : ''"
                  @click="openDialog('revoke', admin)"
                >撤销权限</button>
                <span v-else class="protected-text">受保护</span>
              </td>
            </tr>
            <tr v-if="!loading && !admins.length"><td colspan="5" class="empty-cell">暂无管理员</td></tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel account-panel">
      <div class="panel-title"><div><span>02</span><h2>从 HR 人员库添加</h2></div><p>按姓名或公司工号搜索</p></div>
      <form class="hr-search" @submit.prevent="search">
        <label><span>HR 人员</span><input v-model.trim="keyword" maxlength="64" autocomplete="off" placeholder="输入姓名或公司工号" required /></label>
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
      <section class="permission-dialog" role="dialog" aria-modal="true" aria-labelledby="permission-dialog-title">
        <header><div><small>SECURITY CHECK</small><h2 id="permission-dialog-title">{{ dialog.action === 'grant' ? '确认授予管理员权限' : '确认撤销管理员权限' }}</h2></div><button type="button" aria-label="关闭" :disabled="dialog.saving" @click="closeDialog">×</button></header>
        <p>操作对象：<strong>{{ dialog.target?.display_name }}</strong>（{{ dialog.target?.company_no || dialog.target?.username }}）</p>
        <label><span>当前管理员密码</span><input v-model="dialog.password" type="password" maxlength="128" autocomplete="current-password" required autofocus @keyup.enter="confirmPermissionChange" /><small>用于确认是当前登录管理员本人操作，不会保存该密码。</small></label>
        <p v-if="dialog.error" class="dialog-error" role="alert">{{ dialog.error }}</p>
        <footer><button type="button" :disabled="dialog.saving" @click="closeDialog">取消</button><button class="primary" type="button" :disabled="dialog.saving || !dialog.password" @click="confirmPermissionChange">{{ dialog.saving ? '验证并提交中' : '验证密码并确认' }}</button></footer>
      </section>
    </div>
  </div>
</template>

<style scoped>
.admin-security-badge{min-width:220px}.account-notice,.account-error{margin:0 0 18px;padding:12px 16px;border-left:4px solid #24a66a;background:#effaf5;color:#186844;font-size:12px}.account-error{border-color:#d94f4f;background:#fff3f3;color:#9e2f2f}.account-panel{padding:0;overflow:hidden}.account-table{min-width:820px}.account-table th,.account-table td{vertical-align:middle}.source-badge{display:inline-flex;padding:4px 8px;border-radius:999px;background:#e8f6f8;color:#14798a;font-size:10px;font-weight:700}.source-badge.local{background:#f1f2f4;color:#59646e}.danger-link{min-height:30px;padding:0 8px;color:#b23a3a;border:0;background:transparent}.protected-text{color:var(--muted);font-size:11px}.empty-cell{text-align:center!important;color:var(--muted);padding:30px!important}.hr-search{display:grid;grid-template-columns:minmax(220px,480px) max-content;align-items:end;gap:12px;padding:24px 26px}.hr-search label,.permission-dialog label{display:grid;gap:7px}.hr-search label span,.permission-dialog label span{font-size:11px;font-weight:700;color:#425767}.search-results{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;padding:0 26px 24px}.search-results article{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 16px;border:1px solid var(--line);background:#fbfcfd}.search-results article div{display:grid;grid-template-columns:auto auto;gap:3px 10px}.search-results article strong{font-size:14px}.search-results article span{color:var(--blue);font-size:12px}.search-results article small{grid-column:1/-1;color:var(--muted)}.search-results article button{flex:0 0 auto}.search-hint{margin:0;padding:0 26px 24px;color:var(--muted);font-size:12px}.permission-dialog-backdrop{position:fixed;z-index:100;inset:0;display:grid;place-items:center;padding:20px;background:rgba(4,29,45,.55)}.permission-dialog{width:min(480px,100%);padding:24px;border:1px solid var(--line);background:#fff;box-shadow:0 24px 70px rgba(0,0,0,.28)}.permission-dialog header{display:flex;align-items:flex-start;justify-content:space-between}.permission-dialog header small{color:var(--cyan);font-size:9px;font-weight:800;letter-spacing:1.6px}.permission-dialog h2{margin:5px 0 0;font-size:20px}.permission-dialog header button{min-width:32px;min-height:32px;padding:0;border:0;font-size:24px}.permission-dialog>p{margin:22px 0 16px;color:var(--muted);font-size:13px}.permission-dialog label small{color:var(--muted);line-height:1.6}.dialog-error{margin:12px 0 0!important;padding:10px;background:#fff3f3;color:#9e2f2f!important}.permission-dialog footer{display:flex;justify-content:flex-end;gap:10px;margin-top:22px}
@media(max-width:760px){.search-results{grid-template-columns:1fr}.hr-search{grid-template-columns:1fr}.admin-security-badge{min-width:0}}
</style>
