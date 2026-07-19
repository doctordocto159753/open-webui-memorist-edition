<script lang="ts">
	import '$lib/memorist/authTransport';
	import '$lib/memorist/surfaces';

	export let messageId: string = '';
	export let parentMessageId: string = '';
	export let done: boolean = false;
</script>

{#if messageId}
	<div class="w-full" data-memorist-disclosure-host>
		<memorist-message-disclosure
			message-id={messageId}
			parent-message-id={parentMessageId || undefined}
			message-done={done ? 'true' : 'false'}
		/>
	</div>
{/if}
