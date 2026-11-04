"""Compare-and-set claim. Interrupted/ambiguous publications require reconciliation."""
def claim_card(client, card_id, require_approval):
    query = (client.table('cards').update({'publish_status': 'publishing'})
             .eq('id', card_id).eq('published', False)
             .or_('publish_status.is.null,publish_status.eq.ready'))
    if require_approval:
        query = query.eq('approval', 'approved')
    return bool(query.execute().data)
