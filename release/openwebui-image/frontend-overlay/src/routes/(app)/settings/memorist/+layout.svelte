<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';

	import { WEBUI_NAME, mobile, showSidebar, user } from '$lib/stores';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';

	import {
		MEMORIST_SETTINGS_NAVIGATION,
		type MemoristSettingsNavigationItem
	} from '$lib/memorist/surfaces';

	let loaded = false;

	onMount(() => {
		loaded = true;
	});

	$: isAdmin = $user?.role === 'admin';
	$: navigation = MEMORIST_SETTINGS_NAVIGATION.filter(
		(item: MemoristSettingsNavigationItem) => isAdmin || !item.adminOnly
	);
</script>

<svelte:head>
	<title>Memorist • {$WEBUI_NAME}</title>
</svelte:head>

{#if loaded}
	<div
		id="memorist-settings"
		class=" flex flex-col h-screen max-h-[100dvh] flex-1 transition-width duration-200 ease-in-out {$showSidebar
			? 'md:max-w-[calc(100%-var(--sidebar-width))]'
			: ' md:max-w-[calc(100%-49px)]'}  w-full max-w-full"
	>
		<nav class="px-2.5 pt-1.5 backdrop-blur-xl drag-region select-none">
			<div class=" flex items-center gap-1">
				{#if $mobile}
					<div class="{$showSidebar ? 'md:hidden' : ''} flex flex-none items-center self-end">
						<button
							id="sidebar-toggle-button"
							aria-label="Toggle sidebar"
							class=" cursor-pointer flex rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850 transition"
							on:click={() => {
								showSidebar.set(!$showSidebar);
							}}
						>
							<div class=" self-center p-1.5">
								<Sidebar />
							</div>
						</button>
					</div>
				{/if}

				<div class=" flex w-full">
					<div
						class="flex gap-1 scrollbar-none overflow-x-auto w-fit text-center text-sm font-medium rounded-full bg-transparent pt-1"
					>
						<span class="min-w-fit p-1.5 font-semibold select-none">Memorist</span>
						{#each navigation as item (item.href)}
							<a
								draggable="false"
								data-memorist-nav={item.label}
								class="min-w-fit p-1.5 {$page.url.pathname.startsWith(item.href)
									? ''
									: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition select-none"
								href={item.href}>{item.label}</a
							>
						{/each}
					</div>
				</div>
			</div>
		</nav>

		<div class=" pb-1 flex-1 max-h-full overflow-y-auto px-4 md:px-6" id="memorist-settings-content">
			{#if isAdmin}
				<slot />
			{:else}
				<div class="my-10 text-sm" role="alert" data-memorist-unauthorized>
					<p class="font-medium">Administrator access required</p>
					<p class="mt-1 text-gray-500">
						Memorist settings are only available to Open WebUI administrators. Ask your
						administrator for access.
					</p>
				</div>
			{/if}
		</div>
	</div>
{/if}
