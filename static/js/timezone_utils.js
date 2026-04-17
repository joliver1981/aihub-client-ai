// Timezone utilities
const TimezoneUtils = {
    // Detect user's timezone
    getUserTimezone: function() {
        return Intl.DateTimeFormat().resolvedOptions().timeZone;
    },
    
    // Convert UTC ISO string to local moment
    utcToLocal: function(utcDateString) {
        if (!utcDateString) return null;
        // Assume the database returns UTC times
        return moment.utc(utcDateString).local();
    },
    
    // Format date for display
    formatDate: function(utcDateString, format = 'MMM DD, YYYY h:mm A') {
        const localMoment = this.utcToLocal(utcDateString);
        if (!localMoment) return 'N/A';
        return localMoment.format(format);
    },
    
    // Get relative time (e.g., "2 hours ago")
    getRelativeTime: function(utcDateString) {
        const localMoment = this.utcToLocal(utcDateString);
        if (!localMoment) return 'N/A';
        return localMoment.fromNow();
    },
    
    // Calculate time remaining
    getTimeRemaining: function(utcDueDateString) {
        if (!utcDueDateString) return null;
        
        const dueDate = this.utcToLocal(utcDueDateString);
        const now = moment();
        const diff = dueDate.diff(now);
        
        if (diff <= 0) {
            return { text: 'Overdue', class: 'text-danger', expired: true };
        }
        
        const duration = moment.duration(diff);
        const days = Math.floor(duration.asDays());
        const hours = duration.hours();
        const minutes = duration.minutes();
        
        let text = '';
        let cssClass = 'safe';
        
        if (days > 0) {
            text = `${days}d ${hours}h`;
            cssClass = 'safe';
        } else if (hours > 2) {
            text = `${hours}h ${minutes}m`;
            cssClass = 'safe';
        } else if (hours > 0) {
            text = `${hours}h ${minutes}m`;
            cssClass = 'warning';
        } else {
            text = `${minutes}m`;
            cssClass = 'text-danger';
        }
        
        return { text: text, class: cssClass, expired: false };
    },
    
    // Check if date is today in local timezone
    isToday: function(utcDateString) {
        if (!utcDateString) return false;
        const localDate = this.utcToLocal(utcDateString);
        return localDate.isSame(moment(), 'day');
    },
    
    // Check if date is in current week
    isThisWeek: function(utcDateString) {
        if (!utcDateString) return false;
        const localDate = this.utcToLocal(utcDateString);
        return localDate.isSame(moment(), 'week');
    },
    
    // Check if date is in current month
    isThisMonth: function(utcDateString) {
        if (!utcDateString) return false;
        const localDate = this.utcToLocal(utcDateString);
        return localDate.isSame(moment(), 'month');
    }
};
