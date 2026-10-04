document.addEventListener('DOMContentLoaded', () => {
	const cookieNotice = document.querySelector('[data-cookie-notice]');
	const cookieSettings = cookieNotice && cookieNotice.querySelector('[data-cookie-settings]');
	const analyticsChoice = cookieNotice && cookieNotice.querySelector('[data-cookie-analytics]');
	const advertisingChoice = cookieNotice && cookieNotice.querySelector('[data-cookie-advertising]');
	const readCookieConsent = () => {
		const entry = document.cookie.split('; ').find((cookie) => cookie.startsWith('misha_cookie_consent='));
		if (!entry) return null;
		try {
			const preferences = JSON.parse(decodeURIComponent(entry.slice('misha_cookie_consent='.length)));
			return {analytics: preferences.analytics === true, advertising: preferences.advertising === true};
		} catch {
			return null;
		}
	};
	let cookieConsentState = readCookieConsent();
	const setGoogleConsent = (preferences) => {
		if (typeof window.gtag === 'function') {
			window.gtag('consent', 'update', {
				analytics_storage: preferences.analytics ? 'granted' : 'denied',
				ad_storage: preferences.advertising ? 'granted' : 'denied',
				ad_user_data: preferences.advertising ? 'granted' : 'denied',
				ad_personalization: preferences.advertising ? 'granted' : 'denied',
			});
		}
	};
	const loadGoogleTags = (preferences) => {
		const ids = window.mishaGoogleTagIds || {};
		const analyticsId = preferences.analytics ? ids.analytics : '';
		const advertisingId = preferences.advertising ? ids.advertising : '';
		if (!analyticsId && !advertisingId) return;

		if (typeof window.gtag !== 'function') {
			window.dataLayer = window.dataLayer || [];
			window.gtag = function(){window.dataLayer.push(arguments);};
			window.gtag('js', new Date());
		}
		setGoogleConsent(preferences);
		window.mishaConfiguredGoogleTags = window.mishaConfiguredGoogleTags || {};
		if (analyticsId && !window.mishaConfiguredGoogleTags.analytics) {
			window.gtag('config', analyticsId);
			window.mishaConfiguredGoogleTags.analytics = true;
		}
		if (advertisingId && !window.mishaConfiguredGoogleTags.advertising) {
			window.gtag('config', advertisingId);
			window.mishaConfiguredGoogleTags.advertising = true;
		}
		if (!window.mishaGoogleScriptLoaded) {
			const script = document.createElement('script');
			script.async = true;
			script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(analyticsId || advertisingId)}`;
			document.head.appendChild(script);
			window.mishaGoogleScriptLoaded = true;
		}
	};
	const saveCookieConsent = (preferences) => {
		cookieConsentState = {analytics: Boolean(preferences.analytics), advertising: Boolean(preferences.advertising)};
		const secure = window.location.protocol === 'https:' ? '; Secure' : '';
		document.cookie = `misha_cookie_consent=${encodeURIComponent(JSON.stringify(cookieConsentState))}; Max-Age=15552000; Path=/; SameSite=Lax${secure}`;
		if (cookieNotice) cookieNotice.hidden = true;
		setGoogleConsent(cookieConsentState);
		loadGoogleTags(cookieConsentState);
	};
	if (cookieNotice) {
		if (cookieConsentState) {
			cookieNotice.hidden = true;
			analyticsChoice.checked = cookieConsentState.analytics;
			advertisingChoice.checked = cookieConsentState.advertising;
			loadGoogleTags(cookieConsentState);
		} else {
			cookieNotice.hidden = false;
		}
		cookieNotice.querySelector('[data-cookie-reject]').addEventListener('click', () => saveCookieConsent({analytics: false, advertising: false}));
		cookieNotice.querySelector('[data-cookie-accept]').addEventListener('click', () => saveCookieConsent({analytics: true, advertising: true}));
		cookieNotice.querySelector('[data-cookie-settings-toggle]').addEventListener('click', (event) => {
			cookieSettings.hidden = !cookieSettings.hidden;
			event.currentTarget.setAttribute('aria-expanded', String(!cookieSettings.hidden));
		});
		cookieNotice.querySelector('[data-cookie-save]').addEventListener('click', () => saveCookieConsent({analytics: analyticsChoice.checked, advertising: advertisingChoice.checked}));
		document.querySelector('[data-cookie-reopen]').addEventListener('click', () => {
			analyticsChoice.checked = Boolean(cookieConsentState && cookieConsentState.analytics);
			advertisingChoice.checked = Boolean(cookieConsentState && cookieConsentState.advertising);
			cookieNotice.hidden = false;
			cookieSettings.hidden = false;
			cookieNotice.querySelector('[data-cookie-settings-toggle]').setAttribute('aria-expanded', 'true');
		});
	}

	const menuButton = document.querySelector('.menu-toggle');
	const navigation = document.querySelector('.site-header nav');
	if (menuButton && navigation) {
		menuButton.addEventListener('click', () => {
			const isOpen = navigation.classList.toggle('open');
			menuButton.setAttribute('aria-expanded', String(isOpen));
			menuButton.textContent = isOpen ? '✕' : '☰';
		});
	}

	document.querySelectorAll('.filters select[name="collection"], .filters select[name="sort"], .catalogue-search select[name="collection"]').forEach((select) => {
		select.addEventListener('change', () => {
			if (select.form) select.form.requestSubmit();
		});
	});

	document.querySelectorAll('video[data-playback-rate]').forEach((video) => {
		const rate = parseFloat(video.dataset.playbackRate) || 1;
		const applyRate = () => { video.playbackRate = rate; };
		video.addEventListener('loadedmetadata', applyRate);
		video.addEventListener('play', applyRate);
		applyRate();
	});

	const shipToDifferentAddress = document.querySelector('#id_ship_to_different_address');
	const shippingAddressFields = document.querySelector('[data-shipping-address-fields]');
	const billingAddressSelect = document.querySelector('#id_billing_address');
	const shippingAddressSelect = document.querySelector('#id_shipping_address');
	const billingNewFields = document.querySelector('[data-billing-new-fields]');
	const shippingNewFields = document.querySelector('[data-shipping-new-fields]');
	const updateAddressFields = () => {
		if (shippingAddressFields) shippingAddressFields.hidden = !shipToDifferentAddress.checked;
		if (billingNewFields) billingNewFields.hidden = Boolean(billingAddressSelect && billingAddressSelect.value);
		if (shippingNewFields) shippingNewFields.hidden = Boolean(shippingAddressSelect && shippingAddressSelect.value);
	};
	if (shipToDifferentAddress) {
		shipToDifferentAddress.addEventListener('change', updateAddressFields);
		shippingAddressSelect && shippingAddressSelect.addEventListener('change', updateAddressFields);
		billingAddressSelect && billingAddressSelect.addEventListener('change', updateAddressFields);
		updateAddressFields();
	}

	const deliverySelect = document.querySelector('#id_shipping_method');
	const shippingCountry = document.querySelector('#id_shipping_country');
	const billingCountry = document.querySelector('#id_billing_country');
	const deliveryRatesElement = document.querySelector('#delivery-rates');
	const countryShippingElement = document.querySelector('#country-shipping-config');
	const shippingRateDataElement = document.querySelector('#shipping-rates');
	const parcelMetricsElement = document.querySelector('#parcel-metrics');
	const subtotalElement = document.querySelector('#checkout-subtotal');
	const deliveryCostElement = document.querySelector('#checkout-delivery-cost');
	const totalElement = document.querySelector('#checkout-total');
	if (deliverySelect && deliveryRatesElement && subtotalElement && deliveryCostElement && totalElement) {
		const rates = JSON.parse(deliveryRatesElement.textContent);
		const countryShipping = countryShippingElement ? JSON.parse(countryShippingElement.textContent) : {tiers: []};
		const shippingRates = shippingRateDataElement ? JSON.parse(shippingRateDataElement.textContent) : [];
		const parcel = parcelMetricsElement ? JSON.parse(parcelMetricsElement.textContent) : null;
		const savedAddressesElement = document.querySelector('#saved-checkout-addresses');
		const savedAddresses = savedAddressesElement ? JSON.parse(savedAddressesElement.textContent) : {billing: [], shipping: []};
		const deliveryNote = document.querySelector('[data-delivery-note]');
		const subtotal = Number(subtotalElement.dataset.subtotal || 0);
		const destinationCountry = () => {
			const shippingChosen = Boolean(shipToDifferentAddress && shipToDifferentAddress.checked);
			const addressSelect = shippingChosen ? shippingAddressSelect : billingAddressSelect;
			const countryField = shippingChosen ? shippingCountry : billingCountry;
			const addressList = shippingChosen ? savedAddresses.shipping : savedAddresses.billing;
			const selectedAddress = addressList.find((address) => String(address.id) === (addressSelect && addressSelect.value));
			return selectedAddress ? selectedAddress.country : countryField && countryField.value;
		};
		const shippingRuleForCountry = (country) => {
			const normalizedCountry = (country || '').trim().toLowerCase();
			if (!normalizedCountry) return null;
			return countryShipping.tiers.find((tier) => tier.tier !== 'international'
				&& tier.countries.some((name) => name.toLowerCase() === normalizedCountry))
				|| countryShipping.tiers.find((tier) => tier.tier === 'international');
		};
		const updateDeliveryOptions = () => {
			const country = destinationCountry();
			const countryRule = shippingRuleForCountry(country);
			const countryRates = shippingRates.filter((rate) => country && rate.country.toLowerCase() === country.toLowerCase());
			const serviceCodes = new Set(countryRates.map((rate) => rate.service__code));
			let availableRates = serviceCodes.size ? rates.filter((rate) => serviceCodes.has(rate.code)) : rates;
			if (countryRule) {
				const tierCodes = countryRule.tier === 'uk' ? [countryRule.service_code, 'express_uk'] : [countryRule.service_code];
				availableRates = availableRates.filter((rate) => tierCodes.includes(rate.code));
			}
			const currentCode = deliverySelect.value;
			deliverySelect.replaceChildren();
			if (!availableRates.length) {
				deliverySelect.add(new Option('No delivery service configured for this destination', ''));
				deliverySelect.disabled = true;
				return;
			}
			deliverySelect.disabled = false;
			availableRates.forEach((rate) => {
				const label = countryRule && rate.code === countryRule.service_code ? countryRule.label : rate.label;
				deliverySelect.add(new Option(label, rate.code));
			});
			deliverySelect.value = availableRates.some((rate) => rate.code === currentCode) ? currentCode : availableRates[0].code;
		};
		const updateDeliveryTotal = () => {
			const selected = rates.find((rate) => rate.code === deliverySelect.value);
			if (!selected) {
				deliveryCostElement.textContent = 'Unavailable';
				totalElement.textContent = 'Unavailable';
				if (deliveryNote) deliveryNote.textContent = 'No delivery service is configured for this destination.';
				return;
			}
			const freeOver = selected.free_over === null ? null : Number(selected.free_over);
			const country = destinationCountry();
			const countryRule = shippingRuleForCountry(country);
			const matchedRate = parcel && country
				? shippingRates.find((rate) => rate.country.toLowerCase() === country.toLowerCase()
					&& rate.service__code === deliverySelect.value
					&& Number(rate.max_weight_grams) >= Number(parcel.weight_grams)
					&& Number(rate.max_length_cm) >= Number(parcel.length_cm)
					&& Number(rate.max_width_cm) >= Number(parcel.width_cm)
					&& Number(rate.max_height_cm) >= Number(parcel.height_cm))
				: null;
			const tierPrice = countryRule && selected.code === countryRule.service_code ? Number(countryRule.price) : null;
			const delivery = matchedRate
				? Number(matchedRate.price)
				: tierPrice !== null ? tierPrice
					: freeOver !== null && subtotal >= freeOver ? 0 : Number(selected.price);
			deliveryCostElement.textContent = delivery === 0 ? 'Free' : `£${delivery.toFixed(2)}`;
			totalElement.textContent = `£${(subtotal + delivery).toFixed(2)}`;
			if (deliveryNote) {
				deliveryNote.textContent = matchedRate
					? `Matched parcel rate: ${parcel.weight_grams} g, ${parcel.length_cm} × ${parcel.width_cm} × ${parcel.height_cm} cm.`
					: tierPrice !== null
						? `${countryRule.label} applies. Delivery times vary by destination.`
					: country && shippingRates.some((rate) => rate.country.toLowerCase() === country.toLowerCase())
						? 'No parcel band fits these dimensions; the current service price is shown.'
						: 'No destination-specific rates are configured; the current service price is shown.';
			}
		};
		const refreshDelivery = () => {
			updateDeliveryOptions();
			updateDeliveryTotal();
		};
		deliverySelect.addEventListener('change', updateDeliveryTotal);
		if (shippingCountry) shippingCountry.addEventListener('change', refreshDelivery);
		if (billingCountry) billingCountry.addEventListener('change', refreshDelivery);
		if (shipToDifferentAddress) shipToDifferentAddress.addEventListener('change', refreshDelivery);
		if (billingAddressSelect) billingAddressSelect.addEventListener('change', refreshDelivery);
		if (shippingAddressSelect) shippingAddressSelect.addEventListener('change', refreshDelivery);
		updateDeliveryOptions();
		updateDeliveryTotal();
	}

	const analyticsTable = document.querySelector('[data-analytics-table]');
	if (analyticsTable) {
		const getRows = () => [...analyticsTable.querySelectorAll('tbody tr[data-filterable]')];
		analyticsTable.querySelectorAll('th[data-sort-key]').forEach((header) => {
			header.addEventListener('click', () => {
				const key = header.dataset.sortKey;
				const direction = header.dataset.sortDir === 'asc' ? 'desc' : 'asc';
				analyticsTable.querySelectorAll('th[data-sort-key]').forEach((other) => {
					other.dataset.sortDir = '';
					other.classList.remove('is-sorted-asc', 'is-sorted-desc');
				});
				header.dataset.sortDir = direction;
				header.classList.add(direction === 'asc' ? 'is-sorted-asc' : 'is-sorted-desc');
				const tbody = analyticsTable.querySelector('tbody');
				const sorted = getRows().sort((a, b) => {
					const aValue = (a.dataset[key] || '').toLowerCase();
					const bValue = (b.dataset[key] || '').toLowerCase();
					if (aValue < bValue) return direction === 'asc' ? -1 : 1;
					if (aValue > bValue) return direction === 'asc' ? 1 : -1;
					return 0;
				});
				sorted.forEach((row) => tbody.appendChild(row));
			});
		});
	}

	document.querySelectorAll('.message').forEach((message) => {
		window.setTimeout(() => {
			message.classList.add('is-hidden');
			window.setTimeout(() => message.remove(), 400);		}, 2500);
	});

	document.addEventListener('click', (event) => {
		const target = event.target.closest('a, button');
		if (!target || !cookieConsentState || !cookieConsentState.analytics || !window.mishaAnalyticsUrl || target.closest('.control-nav, [data-store-chat], [data-cookie-notice], [data-cookie-reopen]')) return;
		const payload = JSON.stringify({path: window.location.pathname, target: (target.innerText || target.getAttribute('aria-label') || target.href || '').trim().slice(0, 255), product_id: target.dataset.productId || null});
		if (navigator.sendBeacon) navigator.sendBeacon(window.mishaAnalyticsUrl, new Blob([payload], {type: 'application/json'}));
	});

	const orderModal = document.querySelector('.order-modal');
	const orderModalPanel = orderModal && orderModal.querySelector('.order-modal-panel');
	const closeOrderModal = () => {
		if (!orderModal) return;
		orderModal.hidden = true;
		orderModalPanel.innerHTML = '';
		document.body.classList.remove('modal-open');
	};
	document.querySelectorAll('[data-order-modal]').forEach((button) => {
		button.addEventListener('click', (event) => {
			const template = document.getElementById(button.dataset.orderModal);
			if (!template || !orderModalPanel) return;
			orderModalPanel.innerHTML = template.innerHTML;
			orderModal.hidden = false;
			document.body.classList.add('modal-open');
			orderModalPanel.querySelector('.modal-close').addEventListener('click', closeOrderModal);
			const managementForm = orderModalPanel.querySelector('[data-order-management-form]');
			if (managementForm) {
				const statusField = managementForm.querySelector('[name="status"]');
				const trackingField = managementForm.querySelector('[name="tracking_number"]');
				const saveButton = managementForm.querySelector('[data-order-save]');
				const trackingLabel = trackingField && managementForm.querySelector(`label[for="${trackingField.id}"]`);
				const syncShippingRequirement = () => {
					const missingTracking = statusField && trackingField && statusField.value === 'shipped' && !trackingField.value.trim();
					if (trackingField) {
						trackingField.required = statusField && statusField.value === 'shipped';
						trackingField.classList.toggle('is-required-error', missingTracking);
					}
					if (trackingLabel) trackingLabel.classList.toggle('is-required-error', missingTracking);
					if (saveButton) saveButton.disabled = missingTracking;
				};
				statusField && statusField.addEventListener('change', syncShippingRequirement);
				trackingField && trackingField.addEventListener('input', syncShippingRequirement);
				syncShippingRequirement();
			}
		});
	});
	const selectAll = document.querySelector('[data-select-all]');
	if (selectAll) {
		selectAll.addEventListener('change', () => {
			document.querySelectorAll('[data-order-checkbox]').forEach((checkbox) => {
				checkbox.checked = selectAll.checked;
			});
		});
	}
	if (orderModal) {
		orderModal.querySelector('.order-modal-backdrop').addEventListener('click', closeOrderModal);
		document.addEventListener('keydown', (event) => {
			if (event.key === 'Escape') closeOrderModal();
		});
	}
	document.addEventListener('click', (event) => {
		const copyButton = event.target.closest('[data-copy-target]');
		if (!copyButton) return;
		const input = document.getElementById(copyButton.dataset.copyTarget);
		if (!input) return;
		navigator.clipboard.writeText(input.value).then(() => {
			const originalText = copyButton.textContent;
			copyButton.textContent = 'Copied';
			window.setTimeout(() => { copyButton.textContent = originalText; }, 1600);
		});
	});

	const productModal = document.querySelector('[data-product-modal]');
	const closeProductModal = () => {
		if (!productModal) return;
		productModal.hidden = true;
		document.body.classList.remove('modal-open');
	};
	const openProductModal = () => {
		if (!productModal) return;
		productModal.hidden = false;
		document.body.classList.add('modal-open');
	};
	document.querySelectorAll('[data-product-modal-open]').forEach((button) => button.addEventListener('click', openProductModal));
	document.querySelectorAll('[data-product-modal-close]').forEach((button) => button.addEventListener('click', closeProductModal));
	if (productModal && productModal.dataset.open === 'true') openProductModal();
	if (productModal) productModal.addEventListener('click', (event) => {
		if (event.target.closest('[data-product-modal-close]')) closeProductModal();
	});

	const notifyModal = document.querySelector('[data-notify-modal]');
	const closeNotifyModal = () => {
		if (!notifyModal) return;
		notifyModal.hidden = true;
		document.body.classList.remove('modal-open');
	};
	const openNotifyModal = () => {
		if (!notifyModal) return;
		notifyModal.hidden = false;
		document.body.classList.add('modal-open');
		const emailInput = notifyModal.querySelector('input[name="email"]');
		if (emailInput) emailInput.focus();
	};
	document.querySelectorAll('[data-notify-open]').forEach((button) => button.addEventListener('click', openNotifyModal));
	if (notifyModal) {
		notifyModal.addEventListener('click', (event) => {
			if (event.target.closest('[data-notify-close]')) closeNotifyModal();
		});
		document.addEventListener('keydown', (event) => {
			if (event.key === 'Escape') closeNotifyModal();
		});
	}

	document.addEventListener('keydown', (event) => {
		if (event.key === 'Escape') closeProductModal();
	});

	document.querySelectorAll('form').forEach((form) => {
		if (new URL(form.action, location.href).pathname !== '/login/' || !form.querySelector('input[name="password"]')) return;
		const submitButtons = [...form.querySelectorAll('button[type="submit"], input[type="submit"]')];
		form.addEventListener('submit', (event) => {
			if (form.dataset.submitting === 'true') {
				event.preventDefault();
				return;
			}
			form.dataset.submitting = 'true';
			submitButtons.forEach((button) => { button.disabled = true; });
		});
		window.addEventListener('pageshow', () => {
			delete form.dataset.submitting;
			submitButtons.forEach((button) => { button.disabled = false; });
		});
	});

	document.querySelectorAll('[data-gallery]').forEach((gallery) => {
		const images = [...gallery.querySelectorAll('[data-gallery-image]')];
		const thumbnails = [...gallery.querySelectorAll('[data-gallery-thumbnail]')];
		if (!images.length) return;
		const lightbox = document.querySelector('[data-gallery-lightbox]');
		const lightboxImage = lightbox && lightbox.querySelector('[data-gallery-lightbox-image]');
		const lightboxCount = lightbox && lightbox.querySelector('[data-gallery-lightbox-count]');
		let current = 0;
		const showImage = (index) => {
			current = (index + images.length) % images.length;
			images.forEach((image, imageIndex) => { image.hidden = imageIndex !== current; });
			thumbnails.forEach((thumbnail, thumbnailIndex) => { thumbnail.classList.toggle('is-active', thumbnailIndex === current); });
			if (lightboxImage) {
				lightboxImage.src = images[current].src;
				lightboxImage.alt = images[current].alt;
				lightboxCount.textContent = `${current + 1} / ${images.length}`;
			}
		};
		gallery.querySelector('[data-gallery-previous]')?.addEventListener('click', () => showImage(current - 1));
		gallery.querySelector('[data-gallery-next]')?.addEventListener('click', () => showImage(current + 1));
		thumbnails.forEach((thumbnail) => thumbnail.addEventListener('click', () => showImage(Number(thumbnail.dataset.galleryThumbnail))));
		const openLightbox = () => {
			if (!lightbox) return;
			lightbox.showModal();
			document.body.classList.add('modal-open');
		};
		images.forEach((image) => {
			image.addEventListener('click', openLightbox);
			image.addEventListener('keydown', (event) => {
				if (event.key === 'Enter' || event.key === ' ') {
					event.preventDefault();
					openLightbox();
				}
			});
		});
		if (lightbox) {
			lightbox.querySelector('[data-gallery-lightbox-close]').addEventListener('click', () => lightbox.close());
			lightbox.querySelector('[data-gallery-lightbox-previous]').addEventListener('click', () => showImage(current - 1));
			lightbox.querySelector('[data-gallery-lightbox-next]').addEventListener('click', () => showImage(current + 1));
			lightbox.addEventListener('keydown', (event) => {
				if (event.key === 'Escape') {
					event.preventDefault();
					lightbox.close();
				} else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
					event.preventDefault();
					showImage(current + (event.key === 'ArrowRight' ? 1 : -1));
				}
			});
			lightbox.addEventListener('click', (event) => {
				const bounds = lightbox.getBoundingClientRect();
				if (event.target === lightbox && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) lightbox.close();
			});
			lightbox.addEventListener('close', () => {
				document.body.classList.remove('modal-open');
				images[current].focus();
			});
		}
		showImage(0);
	});

	document.querySelectorAll('[data-account-tab]').forEach((tab) => {
		tab.addEventListener('click', () => {
			const name = tab.dataset.accountTab;
			document.querySelectorAll('[data-account-tab]').forEach((item) => item.classList.toggle('is-active', item === tab));
			document.querySelectorAll('[data-account-panel]').forEach((panel) => {
				const active = panel.dataset.accountPanel === name;
				panel.hidden = !active;
				panel.classList.toggle('is-active', active);
			});
		});
	});

	const storeChat = document.querySelector('[data-store-chat]');
	if (storeChat) {
		const panel = storeChat.querySelector('[data-chat-panel]');
		const toggle = storeChat.querySelector('[data-chat-toggle]');
		const close = storeChat.querySelector('[data-chat-close]');
		const messages = storeChat.querySelector('[data-chat-messages]');
		const form = storeChat.querySelector('[data-chat-form]');
		const input = form.querySelector('[name="message"]');
		const aiConsent = storeChat.querySelector('[data-chat-ai-consent]');
		const csrfToken = form.querySelector('[name="csrfmiddlewaretoken"]').value;
		let busy = false;

		const addMessage = (text, sender = 'assistant') => {
			const message = document.createElement('p');
			message.className = `store-chat-message ${sender}`;
			message.textContent = text;
			messages.appendChild(message);
			messages.scrollTop = messages.scrollHeight;
		};

		const addLink = (label, url) => {
			try {
				const linkUrl = new URL(url, window.location.origin);
				if (!['http:', 'https:'].includes(linkUrl.protocol)) return;
				const link = document.createElement('a');
				link.className = 'store-chat-link';
				link.href = linkUrl.href;
				link.textContent = label;
				if (linkUrl.origin !== window.location.origin) {
					link.target = '_blank';
					link.rel = 'noopener noreferrer';
				}
				messages.appendChild(link);
			} catch {
				return;
			}
		};

		const setBusy = (state) => {
			busy = state;
			form.querySelector('button[type="submit"]').disabled = state;
			input.disabled = state;
			storeChat.querySelectorAll('.store-chat-order-form button, .store-chat-contact-form button').forEach((button) => { button.disabled = state; });
		};

		const ask = async (payload) => {
			setBusy(true);
			try {
				const response = await fetch(storeChat.dataset.endpoint, {
					method: 'POST',
					headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrfToken},
					body: JSON.stringify(payload),
				});
				const data = await response.json();
				addMessage(data.reply || 'Sorry, I could not complete that request.');
				if (data.contact_submitted) messages.querySelectorAll('.store-chat-contact-form').forEach((contactForm) => contactForm.remove());
				(data.products || []).forEach((product) => addLink(product.name, product.url));
				if (data.contact_url) addLink('Contact us', data.contact_url);
				if (data.checkout_url) addLink('Go to checkout', data.checkout_url);
				if (data.tracking_url) addLink('Open carrier tracking', data.tracking_url);
				if (data.needs_order_details) showOrderForm();
				if (data.needs_contact_form) showContactForm();
				if (data.needs_ai_consent && aiConsent) {
					input.value = payload.message || '';
					aiConsent.closest('.store-chat-ai-notice').classList.add('is-required');
					aiConsent.focus();
				}
			} catch {
				addMessage('I could not connect just now. Please try again or contact us.');
			} finally {
				setBusy(false);
				input.focus();
			}
		};

		const showOrderForm = () => {
			const orderForm = document.createElement('form');
			orderForm.className = 'store-chat-order-form';
			orderForm.innerHTML = '<label>Six-digit order reference<input name="reference" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" required></label><label>Checkout email<input name="email" type="email" maxlength="254" required></label><button type="submit">Check order</button>';
			orderForm.addEventListener('submit', (event) => {
				event.preventDefault();
				if (busy) return;
				const details = new FormData(orderForm);
				orderForm.remove();
				addMessage('Check order status', 'customer');
				ask({action: 'track_order', message: 'track my order', reference: details.get('reference'), email: details.get('email')});
			});
			messages.appendChild(orderForm);
			messages.scrollTop = messages.scrollHeight;
			orderForm.querySelector('input').focus();
		};

		const showContactForm = () => {
			const existingForm = messages.querySelector('.store-chat-contact-form');
			if (existingForm) {
				existingForm.querySelector('input').focus();
				return;
			}
			const contactForm = document.createElement('form');
			contactForm.className = 'store-chat-contact-form';
			contactForm.innerHTML = '<label>Name<input name="name" maxlength="120" autocomplete="name" required></label><label>Email<input name="email" type="email" maxlength="254" autocomplete="email" required></label><label>Phone (optional)<input name="phone" type="tel" maxlength="50" autocomplete="tel"></label><label>Subject (optional)<input name="subject" maxlength="200"></label><label>Message<textarea name="message" rows="3" maxlength="5000" required></textarea></label><button type="submit">Send message</button>';
			contactForm.addEventListener('submit', (event) => {
				event.preventDefault();
				if (busy) return;
				const details = new FormData(contactForm);
				ask({
					action: 'contact',
					name: details.get('name'),
					email: details.get('email'),
					phone: details.get('phone'),
					subject: details.get('subject'),
					message: details.get('message'),
				});
			});
			messages.appendChild(contactForm);
			messages.scrollTop = messages.scrollHeight;
			contactForm.querySelector('input').focus();
		};

		const submitMessage = (message, allowAi = false) => {
			const value = message.trim();
			if (!value || busy) return;
			addMessage(value, 'customer');
			ask({message: value, ai_consent: allowAi});
		};

		toggle.addEventListener('click', () => {
			panel.hidden = !panel.hidden;
			toggle.setAttribute('aria-expanded', String(!panel.hidden));
			if (!panel.hidden) input.focus();
		});
		close.addEventListener('click', () => {
			panel.hidden = true;
			toggle.setAttribute('aria-expanded', 'false');
			toggle.focus();
		});
		form.addEventListener('submit', (event) => {
			event.preventDefault();
			const value = input.value;
			input.value = '';
			submitMessage(value, Boolean(aiConsent && aiConsent.checked));
		});
		if (aiConsent) aiConsent.addEventListener('change', () => aiConsent.closest('.store-chat-ai-notice').classList.remove('is-required'));
		storeChat.querySelectorAll('[data-chat-suggestion]').forEach((button) => button.addEventListener('click', () => submitMessage(button.dataset.chatSuggestion, Boolean(aiConsent && aiConsent.checked))));
	}
});
