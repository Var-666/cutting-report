<script setup lang="ts">
import type { User } from '../types/auth'

import { toError } from '../utils/errors'
import { nextTick, reactive, ref } from 'vue'
import { NButton, NInput } from 'naive-ui'
import { bootstrapAccount, loginAccount } from '../api/reports'
import loginIllustration from '../assets/login-illustration.png'
import projectLogo from '../assets/project-logo.png'

const props = defineProps({
  setupRequired: Boolean,
  workerDatabaseAuthEnabled: Boolean,
})
const emit = defineEmits<{ authenticated: [user: User] }>()
const loading = ref(false)
const error = ref('')
const errorElement = ref<HTMLElement | null>(null)
const form = reactive({ username: '', display_name: '', password: '' })

async function submit() {
  form.username = form.username.trim()
  form.display_name = form.display_name.trim()
  loading.value = true
  error.value = ''
  try {
    let result
    if (props.setupRequired) {
      result = await bootstrapAccount(form)
    } else {
      result = await loginAccount({
        username: form.username,
        password: form.password,
      })
    }
    emit('authenticated', result.user)
  } catch (caught) {
    const exception = toError(caught)
    error.value = exception.message
    await nextTick()
    errorElement.value?.focus()
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-scene" aria-label="生产报表数据可视化">
      <div class="login-scene__brand"><img class="brand-logo brand-logo--login" :src="projectLogo" alt="" width="54" height="54" /><strong>切割生产智能产报系统</strong></div>
      <img class="login-scene__illustration" :src="loginIllustration" alt="生产报表与数据分析插画" width="2048" height="2048" />
    </section>
    <section class="login-card">
      <p class="eyebrow">WORKSHOP PRODUCTION</p>
      <h1>{{ setupRequired ? '初始化管理员' : '系统登录' }}</h1>
      <p>{{ setupRequired ? '首次使用需要创建一个本地管理员账号，完成后可从 HR 人员库继续授权管理员。' : workerDatabaseAuthEnabled ? '使用公司工号或管理员账号登录，系统会自动识别账号权限并进入对应页面。' : '使用管理员账号和密码登录。' }}</p>
      <form @submit.prevent="submit">
        <label><span>{{ !setupRequired && workerDatabaseAuthEnabled ? '公司工号 / 管理员账号' : '管理员账号' }}</span><NInput v-model:value="form.username" name="username" :input-props="{ name: 'username', minlength: setupRequired ? 2 : 1, autocomplete: 'username', spellcheck: false, required: true }" :maxlength="64" /></label>
        <label v-if="setupRequired"><span>显示姓名</span><NInput v-model:value="form.display_name" :input-props="{ name: 'display_name', autocomplete: 'name', required: true }" :maxlength="64" /></label>
        <label><span>密码</span><NInput v-model:value="form.password" type="password" show-password-on="click" :input-props="{ name: 'password', minlength: setupRequired ? 8 : 1, autocomplete: setupRequired ? 'new-password' : 'current-password', required: true }" :maxlength="128" /></label>
        <div v-if="error" ref="errorElement" class="login-error" role="alert" tabindex="-1">{{ error }}，请检查账号和密码后重试。</div>
        <NButton type="primary" attr-type="submit" :loading="loading" :disabled="loading" block>{{ loading ? '正在提交' : setupRequired ? '创建并登录' : '登录' }}</NButton>
      </form>
    </section>
  </main>
</template>
