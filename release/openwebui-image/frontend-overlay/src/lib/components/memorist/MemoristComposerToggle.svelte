<script lang="ts">
	import { onDestroy } from 'svelte';

	import { chatId, temporaryChatEnabled } from '$lib/stores';
	import '$lib/memorist/authTransport';
	import { mountMemoryWorkflowToggleNearComposer } from '$lib/memorist/surfaces';

	let container: HTMLElement | null = null;
	let mountedChatId = '';

	// The toggle persists per user and chat through the authenticated Memorist
	// proxy; the saved Off state remains a server-side consent ceiling that a
	// forged request cannot override. Temporary chats have no durable chat
	// scope, so no toggle is offered there.
	$: if (container && !$temporaryChatEnabled && $chatId && $chatId !== mountedChatId) {
		container.replaceChildren();
		mountMemoryWorkflowToggleNearComposer(container, $chatId);
		mountedChatId = $chatId;
	}

	$: if (container && (!$chatId || $temporaryChatEnabled) && mountedChatId) {
		container.replaceChildren();
		mountedChatId = '';
	}

	onDestroy(() => {
		container?.replaceChildren();
	});
</script>

<div
	bind:this={container}
	class="flex items-center min-w-0 ml-1"
	data-memorist-composer-toggle
/>
