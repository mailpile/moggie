from .headerprint import HeaderPrints

class KeywordExtractor:
    def __init__(self):
        # FIXME: 
        #   - Make this configurable, somehow
        #   - Plugins?
        #   - Language/locale specific rules?
        pass

    def extract_email_keywords(self, parsed_email):
        """
        The input should be a parsed e-mail, as returned by
        moggie.email.parsemime.

        Returns a tuple of (status, keyword_list), where status will inform
        the caller whether additional processing is requested. 
        """
        keywords = []
        
        # These are synthetic keywords which group together messages
        # that have a similar structure or origin. Mostly for use in
        # the spam filters.
        hp = HeaderPrints(parsed_email)
        for k in ('org', 'sender', 'tools'):
            if k in hp and hp[k]:
                keywords.append('hp_%s:%s' % (k, hp[k]))

        return (None, keywords)
