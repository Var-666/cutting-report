<script setup>
import { reactive, ref } from 'vue'
import { bootstrapAccount, loginAccount, workerLoginAccount } from '../api/reports'

const props = defineProps({
  setupRequired: Boolean,
  workerDatabaseAuthEnabled: Boolean,
})
const emit = defineEmits(['authenticated'])
const loading = ref(false)
const error = ref('')
const mode = ref(props.workerDatabaseAuthEnabled ? 'worker' : 'admin')
const adminForm = reactive({ username: '', display_name: '', password: '' })
const workerForm = reactive({ company_no: '', password: '' })

async function submit() {
  loading.value = true
  error.value = ''
  try {
    let result
    if (props.setupRequired) {
      result = await bootstrapAccount(adminForm)
    } else if (mode.value === 'worker') {
      result = await workerLoginAccount(workerForm)
    } else {
      result = await loginAccount({
        username: adminForm.username,
        password: adminForm.password,
      })
    }
    emit('authenticated', result.user)
  } catch (exception) {
    error.value = exception.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-card">
      <div class="login-brand"><span></span><span></span><span></span></div>
      <p class="eyebrow">WORKSHOP PRODUCTION</p>
      <h1>{{ setupRequired ? '初始化管理员账号' : mode === 'worker' ? '工人登录' : '管理员登录' }}</h1>
      <p>{{ setupRequired ? '首次使用需要创建本系统管理员。工人账号由HR数据库统一验证。' : mode === 'worker' ? '使用HR数据库中的公司工号和密码登录。' : '使用本系统管理员账号进入管理后台。' }}</p>
      <div v-if="!setupRequired && workerDatabaseAuthEnabled" class="login-tabs" role="tablist" aria-label="登录身份">
        <button type="button" :class="{ active: mode === 'worker' }" @click="mode = 'worker'; error = ''">工人登录</button>
        <button type="button" :class="{ active: mode === 'admin' }" @click="mode = 'admin'; error = ''">管理员登录</button>
      </div>
      <form @submit.prevent="submit">
        <template v-if="!setupRequired && mode === 'worker'">
          <label><span>公司工号</span><input v-model.trim="workerForm.company_no" maxlength="64" autocomplete="username" required /></label>
          <label><span>密码</span><input v-model="workerForm.password" type="password" maxlength="128" autocomplete="current-password" required /></label>
        </template>
        <template v-else>
          <label><span>管理员账号</span><input v-model.trim="adminForm.username" minlength="2" maxlength="64" autocomplete="username" required /></label>
          <label v-if="setupRequired"><span>显示姓名</span><input v-model.trim="adminForm.display_name" maxlength="64" required /></label>
          <label><span>密码</span><input v-model="adminForm.password" type="password" minlength="8" maxlength="128" autocomplete="current-password" required /></label>
        </template>
        <div v-if="error" class="login-error">{{ error }}</div>
        <button class="primary" type="submit" :disabled="loading">{{ loading ? '正在提交' : setupRequired ? '创建并登录' : mode === 'worker' ? '工人登录' : '管理员登录' }}</button>
      </form>
    </section>
  </main>
</template>
