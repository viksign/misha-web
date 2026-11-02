document.addEventListener('DOMContentLoaded', () => {
	const staffMenu = document.querySelector('.mobile-staff-menu');
	if (staffMenu) {
		document.addEventListener('click', (event) => {
			if (!staffMenu.contains(event.target)) staffMenu.open = false;
		});
		document.addEventListener('keydown', (event) => {
			if (event.key === 'Escape' && staffMenu.open) {
				staffMenu.open = false;
				staffMenu.querySelector('summary').focus();
			}
		});
		staffMenu.querySelectorAll('a').forEach((link) => {
			link.addEventListener('click', () => { staffMenu.open = false; });
		});
	}
	const productPrice = document.querySelector('[data-product-price]');
	if (productPrice) {
		const optionSelects = document.querySelectorAll('[data-product-option]');
		const breakdown = document.querySelector('[data-price-breakdown]');
		const guidance = document.querySelector('[data-option-guidance]');
		const guidanceLabel = document.querySelector('[data-option-guidance-label]');
		const guidanceCopy = document.querySelector('[data-option-guidance-copy]');
		const money = (pennies) => `£${(pennies / 100).toFixed(2)}`;
		const updateOptionGuidance = (preferredSelect = null) => {
			if (!guidance || !guidanceLabel || !guidanceCopy) return;
			let chosen = null;
			const selects = preferredSelect ? [preferredSelect, ...optionSelects] : optionSelects;
			for (const select of selects) {
				const option = select.selectedOptions[0];
				if (!option) continue;
				const kind = (option.dataset.optionKind || '').trim();
				if (/length|size/i.test(kind)) {
					chosen = { kind, text: (option.dataset.optionDescription || '').trim() };
					break;
				}
			}
			if (!chosen || !chosen.text) {
				guidance.hidden = true;
				guidanceLabel.textContent = '';
				guidanceCopy.textContent = '';
				return;
			}
			guidanceLabel.textContent = `${chosen.kind} recommendation`;
			guidanceCopy.textContent = chosen.text;
			guidance.hidden = false;
		};
		const updateProductPrice = (preferredSelect = null) => {
			const basePennies = Math.round(Number(productPrice.dataset.basePrice) * 100);
			let extras = 0;
			const extraLabels = [];
			optionSelects.forEach((select) => {
				const option = select.selectedOptions[0];
				const cost = Math.round(Number(option.dataset.additionalCost || 0) * 100);
				extras += cost;
				if (cost) extraLabels.push(`${option.textContent.split(' — ')[0]} +${money(cost)}`);
			});
			productPrice.textContent = money(basePennies + extras);
			if (breakdown) {
				breakdown.hidden = !extras;
				breakdown.textContent = extras ? `Base price ${money(basePennies)} · ${extraLabels.join(' · ')}` : '';
			}
			updateOptionGuidance(preferredSelect);
		};
		optionSelects.forEach((select) => select.addEventListener('change', () => updateProductPrice(select)));
		updateProductPrice();
	}
	document.querySelectorAll('[data-option-manager]').forEach((manager) => {
		const totalForms = manager.querySelector('[name="options-TOTAL_FORMS"]');
		const rows = manager.querySelector('[data-option-rows]');
		const emptyMessage = manager.querySelector('[data-option-empty]');
		const kindOf = (row) => row.querySelector('[name$="-kind"]').value.trim().replace(/\s+/g, ' ').toLowerCase();
		manager.querySelector('[data-option-add]').addEventListener('click', () => {
			const index = Number(totalForms.value);
			const template = manager.querySelector('[data-option-template]');
			rows.insertAdjacentHTML('beforeend', template.innerHTML.replaceAll('__prefix__', String(index)));
			totalForms.value = String(index + 1);
			const row = rows.lastElementChild;
			const previous = row.previousElementSibling;
			if (previous) row.querySelector('[name$="-kind"]').value = previous.querySelector('[name$="-kind"]').value;
			if (emptyMessage) emptyMessage.hidden = true;
			row.querySelector(previous ? '[name$="-label"]' : '[name$="-kind"]').focus();
		});
		manager.addEventListener('change', (event) => {
			if (!event.target.matches('[name$="-is_default"]') || !event.target.checked) return;
			const row = event.target.closest('[data-option-row]');
			const kind = kindOf(row);
			rows.querySelectorAll('[data-option-row]').forEach((other) => {
				if (other !== row && kindOf(other) === kind) {
					other.querySelector('[name$="-is_default"]').checked = false;
				}
			});
			row.querySelector('[name$="-additional_cost"]').value = '0.00';
		});
	});

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
		const setNavigationOpen = (isOpen, restoreFocus = false) => {
			navigation.classList.toggle('open', isOpen);
			menuButton.setAttribute('aria-expanded', String(isOpen));
			menuButton.setAttribute('aria-label', isOpen ? 'Close menu' : 'Open menu');
			if (isOpen && staffMenu) staffMenu.open = false;
			if (restoreFocus) menuButton.focus();
		};
		menuButton.addEventListener('click', () => {
			setNavigationOpen(!navigation.classList.contains('open'));
		});
		window.matchMedia('(max-width: 1200px)').addEventListener('change', () => {
			setNavigationOpen(false);
		});
		navigation.querySelectorAll('a').forEach((link) => {
			link.addEventListener('click', () => setNavigationOpen(false));
		});
		document.addEventListener('click', (event) => {
			if (!navigation.contains(event.target) && !menuButton.contains(event.target)) {
				setNavigationOpen(false);
			}
		});
		document.addEventListener('keydown', (event) => {
			if (event.key === 'Escape' && navigation.classList.contains('open')) {
				setNavigationOpen(false, true);
			}
		});
	}

	document.querySelectorAll('[data-tag-picker]').forEach((picker) => {
		const select = picker.querySelector('select');
		const enhanced = picker.querySelector('.tag-picker-enhanced');
		const input = picker.querySelector('.tag-picker-search');
		const toggle = picker.querySelector('.tag-picker-toggle');
		const chips = picker.querySelector('[data-tag-chips]');
		const dropdown = picker.querySelector('.tag-picker-dropdown');
		const list = picker.querySelector('[role="listbox"]');
		const empty = picker.querySelector('.tag-picker-empty');
		const status = picker.querySelector('.tag-picker-status');
		const options = Array.from(select.options);
		let matches = [];
		let activeIndex = -1;
		const setActive = (index) => {
			activeIndex = index;
			Array.from(list.children).forEach((item, position) => {
				item.classList.toggle('is-active', position === index);
				item.setAttribute('aria-selected', String(position === index));
			});
			if (index >= 0) {
				const item = list.children[index];
				input.setAttribute('aria-activedescendant', item.id);
				item.scrollIntoView({block: 'nearest'});
			} else {
				input.removeAttribute('aria-activedescendant');
			}
		};
		const setOpen = (open) => {
			dropdown.hidden = !open;
			input.setAttribute('aria-expanded', String(open));
			toggle.setAttribute('aria-expanded', String(open));
			toggle.setAttribute('aria-label', open ? 'Close available tags' : 'Show available tags');
			if (!open) setActive(-1);
		};
		const renderOptions = () => {
			list.replaceChildren();
			const query = input.value.trim().toLocaleLowerCase();
			matches = options.filter((option) => !option.selected && option.text.toLocaleLowerCase().includes(query));
			matches.forEach((option) => {
				const item = document.createElement('div');
				item.id = `${select.id}-option-${options.indexOf(option)}`;
				item.setAttribute('role', 'option');
				item.setAttribute('aria-selected', 'false');
				item.textContent = option.text;
				item.addEventListener('mousedown', (event) => event.preventDefault());
				item.addEventListener('click', () => addTag(option));
				list.append(item);
			});
			empty.hidden = matches.length > 0;
			setActive(-1);
		};
		const renderChips = () => {
			chips.replaceChildren();
			options.filter((option) => option.selected).forEach((option) => {
				const chip = document.createElement('span');
				chip.className = 'tag-picker-chip';
				const label = document.createElement('span');
				label.textContent = option.text;
				const remove = document.createElement('button');
				remove.type = 'button';
				remove.textContent = '×';
				remove.setAttribute('aria-label', `Remove ${option.text} tag`);
				remove.addEventListener('click', () => {
					option.selected = false;
					renderChips();
					renderOptions();
					input.focus();
					status.textContent = `${option.text} removed.`;
				});
				chip.append(label, remove);
				chips.append(chip);
			});
		};
		const addTag = (option) => {
			option.selected = true;
			input.value = '';
			renderChips();
			renderOptions();
			input.focus();
			setOpen(true);
			status.textContent = `${option.text} added.`;
		};
		input.addEventListener('focus', () => { renderOptions(); setOpen(true); });
		input.addEventListener('input', () => { renderOptions(); setOpen(true); });
		input.addEventListener('keydown', (event) => {
			if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
				event.preventDefault();
				setOpen(true);
				if (matches.length) {
					const next = activeIndex < 0 ? (event.key === 'ArrowDown' ? 0 : matches.length - 1) : (activeIndex + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length;
					setActive(next);
				}
			} else if (event.key === 'Enter') {
				event.preventDefault();
				if (!dropdown.hidden && activeIndex >= 0) addTag(matches[activeIndex]);
			} else if (event.key === 'Escape') {
				event.preventDefault();
				setOpen(false);
			}
		});
		toggle.addEventListener('click', () => {
			const open = dropdown.hidden;
			input.focus();
			renderOptions();
			setOpen(open);
		});
		picker.addEventListener('focusout', (event) => {
			if (!picker.contains(event.relatedTarget)) setOpen(false);
		});
		document.addEventListener('click', (event) => {
			if (!picker.contains(event.target)) setOpen(false);
		});
		select.addEventListener('change', () => { renderChips(); renderOptions(); });
		renderChips();
		renderOptions();
		select.hidden = true;
		picker.querySelector('.tag-picker-native-label').hidden = true;
		enhanced.hidden = false;
	});

	document.querySelectorAll('.catalogue-search select[name="collection"]').forEach((select) => {
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
	const shippingAddressSelect = document.querySelector('#id_shipping_address');
	const billingNewFields = document.querySelector('[data-billing-new-fields]');
	const shippingNewFields = document.querySelector('[data-shipping-new-fields]');
	const checkoutForm = document.querySelector('.checkout-form');
	const updateCheckoutValidation = () => {
		if (!checkoutForm) return;
		checkoutForm.querySelectorAll('input, select, textarea').forEach((field) => {
			const invalid = field.willValidate && !field.validity.valid;
			field.setAttribute('aria-invalid', String(invalid));
		});
	};
	if (checkoutForm) {
		checkoutForm.addEventListener('invalid', () => {
			checkoutForm.classList.add('checkout-validation-attempted');
			updateCheckoutValidation();
		}, true);
		const updateEditedField = (event) => {
			if (!event.target.matches('input, select, textarea')) return;
			if (checkoutForm.classList.contains('checkout-validation-attempted')
				|| event.target.hasAttribute('aria-invalid')) {
				event.target.setAttribute('aria-invalid', String(!event.target.validity.valid));
			}
		};
		checkoutForm.addEventListener('input', updateEditedField);
		checkoutForm.addEventListener('change', updateEditedField);
	}
	const setAddressFieldsRequired = (container, required) => {
		if (!container) return;
		container.querySelectorAll('[data-required-address]').forEach((field) => {
			field.required = required;
			const marker = field.closest('label').querySelector('.checkout-required');
			if (marker) marker.hidden = !required;
			if (!required) field.removeAttribute('aria-invalid');
		});
	};
	const updateAddressFields = () => {
		if (shippingAddressFields) shippingAddressFields.hidden = !shipToDifferentAddress.checked;
		if (shippingNewFields) shippingNewFields.hidden = Boolean(shippingAddressSelect && shippingAddressSelect.value);
		setAddressFieldsRequired(billingNewFields, Boolean(billingNewFields && !billingNewFields.hidden));
		setAddressFieldsRequired(
			shippingNewFields,
			Boolean(shippingAddressFields && !shippingAddressFields.hidden && shippingNewFields && !shippingNewFields.hidden)
		);
		if (checkoutForm && checkoutForm.classList.contains('checkout-validation-attempted')) {
			updateCheckoutValidation();
		}
	};
	if (shipToDifferentAddress) {
		shipToDifferentAddress.addEventListener('change', updateAddressFields);
		shippingAddressSelect && shippingAddressSelect.addEventListener('change', updateAddressFields);
		updateAddressFields();
	}

	const deliverySelect = document.querySelector('#id_shipping_method');
	const shippingCountry = document.querySelector('#id_shipping_country');
	const billingCountry = document.querySelector('#id_billing_country');
	const phoneCountry = document.querySelector('#id_phone_country');
	const phoneNumber = document.querySelector('#id_phone');
	const phoneCountriesElement = document.querySelector('#phone-countries');
	if (phoneCountry && phoneNumber && phoneCountriesElement) {
		const phoneCountries = JSON.parse(phoneCountriesElement.textContent);
		const phonePreview = document.querySelector('[data-phone-preview]');
		const phoneHeading = document.querySelector('.checkout-phone-heading');
		const phoneInfo = document.querySelector('.phone-info');
		if (phoneInfo && phoneHeading) {
			phoneInfo.addEventListener('click', () => {
				phoneHeading.toggleAttribute('data-phone-help-open');
			});
			phoneInfo.addEventListener('keydown', (event) => {
				if (event.key === 'Escape') {
					phoneHeading.removeAttribute('data-phone-help-open');
					phoneInfo.blur();
				}
			});
			[phoneNumber, phoneCountry].forEach((field) => {
				field.addEventListener('invalid', () => phoneHeading.setAttribute('data-phone-error', ''));
			});
		}
		const updatePhonePreview = () => {
			const details = Object.values(phoneCountries).find((country) => country.region === phoneCountry.value);
			if (phonePreview) {
				const digits = phoneNumber.value.replace(/[ ()-]/g, '');
				phonePreview.textContent = details && digits ? `International number: ${details.calling_code}${digits}` : '';
			}
		};
		billingCountry && billingCountry.addEventListener('change', () => {
			const details = phoneCountries[billingCountry.value];
			if (details) phoneCountry.value = details.region;
			updatePhonePreview();
		});
		if (!phoneCountry.value && billingCountry && phoneCountries[billingCountry.value]) {
			phoneCountry.value = phoneCountries[billingCountry.value].region;
		}
		phoneCountry.addEventListener('change', updatePhonePreview);
		phoneNumber.addEventListener('input', updatePhonePreview);
		updatePhonePreview();
	}
	const deliveryRatesElement = document.querySelector('#delivery-rates');
	const countryShippingElement = document.querySelector('#country-shipping-config');
	const shippingRateDataElement = document.querySelector('#shipping-rates');
	const parcelMetricsElement = document.querySelector('#parcel-metrics');
	const subtotalElement = document.querySelector('#checkout-subtotal');
	const deliveryCostElement = document.querySelector('#checkout-delivery-cost');
	const totalElement = document.querySelector('#checkout-total');
	const deliveryServiceElement = document.querySelector('[data-delivery-service]');
	const deliveryTransitElement = document.querySelector('[data-delivery-transit]');
	if (deliverySelect && deliveryRatesElement && subtotalElement && deliveryCostElement && totalElement) {
		const rates = JSON.parse(deliveryRatesElement.textContent);
		const countryShipping = countryShippingElement ? JSON.parse(countryShippingElement.textContent) : {tiers: []};
		const shippingRates = shippingRateDataElement ? JSON.parse(shippingRateDataElement.textContent) : [];
		const parcel = parcelMetricsElement ? JSON.parse(parcelMetricsElement.textContent) : null;
		const savedAddressesElement = document.querySelector('#saved-checkout-addresses');
		const savedAddresses = savedAddressesElement ? JSON.parse(savedAddressesElement.textContent) : {shipping: []};
		const subtotal = Number(subtotalElement.dataset.subtotal || 0);
		const destinationCountry = () => {
			const shippingChosen = Boolean(shipToDifferentAddress && shipToDifferentAddress.checked);
			const countryField = shippingChosen ? shippingCountry : billingCountry;
			const selectedAddress = shippingChosen && savedAddresses.shipping.find(
				(address) => String(address.id) === (shippingAddressSelect && shippingAddressSelect.value)
			);
			return selectedAddress ? selectedAddress.country : countryField && countryField.value;
		};
		const shippingRuleForCountry = (country) => {
			const normalizedCountry = (country || '').trim().toLowerCase();
			if (!normalizedCountry) return null;
			return countryShipping.tiers.find((tier) => tier.tier !== 'international'
				&& tier.countries.some((name) => name.toLowerCase() === normalizedCountry))
				|| countryShipping.tiers.find((tier) => tier.tier === 'international');
		};
		const regionForCountry = (country) => {
			const normalizedCountry = (country || '').trim().toLowerCase();
			const regions = countryShipping.regions || [];
			if (!normalizedCountry || !regions.length) return null;
			return regions.find((region) => region.countries.some((name) => name.toLowerCase() === normalizedCountry))
				|| regions.find((region) => region.is_default)
				|| null;
		};
		const regionNote = document.querySelector('[data-delivery-region]');
		const serviceLabel = (service) => (service.transit ? `${service.label} (${service.transit})` : service.label);
		let activeRates = rates;
		let activeRegion = null;
		const availableRatesForCountry = (country) => {
			const countryRates = shippingRates.filter((rate) => country && rate.country.toLowerCase() === country.toLowerCase());
			const serviceCodes = new Set(countryRates.map((rate) => rate.service__code));
			const region = regionForCountry(country);
			if (region && region.services.length) {
				const narrowed = region.services.filter((service) => serviceCodes.has(service.code));
				return {region, options: narrowed.length ? narrowed : region.services};
			}
			let available = serviceCodes.size ? rates.filter((rate) => serviceCodes.has(rate.code)) : rates;
			const countryRule = shippingRuleForCountry(country);
			if (countryRule) {
				const tierCodes = countryRule.tier === 'uk' ? [countryRule.service_code, 'express_uk'] : [countryRule.service_code];
				available = available.filter((rate) => tierCodes.includes(rate.code));
				available = available.map((rate) => (rate.code === countryRule.service_code
					? {...rate, label: countryRule.label, transit: countryRule.transit, price: countryRule.price, free_over: null}
					: rate));
			}
			return {region, options: available};
		};
		const updateDeliveryOptions = () => {
			const country = destinationCountry();
			const {region, options} = availableRatesForCountry(country);
			activeRates = options;
			activeRegion = region;
			if (regionNote) {
				regionNote.textContent = !country
					? 'Choose your country or region above to see the delivery methods available for your destination.'
					: region
						? `Showing ${region.name} delivery methods for ${country}.`
						: `Showing delivery methods available for ${country}.`;
			}
			const currentCode = deliverySelect.value;
			deliverySelect.replaceChildren();
			if (!options.length) {
				deliverySelect.add(new Option('No delivery service configured for this destination', ''));
				deliverySelect.disabled = true;
				return;
			}
			deliverySelect.disabled = false;
			options.forEach((option) => deliverySelect.add(new Option(serviceLabel(option), option.code)));
			deliverySelect.value = options.some((option) => option.code === currentCode) ? currentCode : options[0].code;
		};
		const updateDeliveryTotal = () => {
			const selected = activeRates.find((rate) => rate.code === deliverySelect.value)
				|| rates.find((rate) => rate.code === deliverySelect.value);
			if (!selected) {
				deliveryCostElement.textContent = 'Unavailable';
				totalElement.textContent = 'Unavailable';
				if (deliveryServiceElement) deliveryServiceElement.textContent = 'No delivery service is available for this destination.';
				if (deliveryTransitElement) {
					deliveryTransitElement.textContent = '';
					deliveryTransitElement.hidden = true;
				}
				return;
			}
			if (deliveryServiceElement) {
				const label = selected.label.trim();
				const regionName = activeRegion && activeRegion.name.trim();
				deliveryServiceElement.textContent = regionName && !label.toLocaleLowerCase().startsWith(regionName.toLocaleLowerCase())
					? `${regionName} ${label}`
					: label;
			}
			if (deliveryTransitElement) {
				deliveryTransitElement.textContent = selected.transit ? `Estimated transit: ${selected.transit}.` : '';
				deliveryTransitElement.hidden = !selected.transit;
			}
			const freeOver = selected.free_over === null || selected.free_over === undefined ? null : Number(selected.free_over);
			const country = destinationCountry();
			const matchedRate = parcel && country
				? shippingRates.find((rate) => rate.country.toLowerCase() === country.toLowerCase()
					&& rate.service__code === deliverySelect.value
					&& Number(rate.max_weight_grams) >= Number(parcel.weight_grams)
					&& Number(rate.max_length_cm) >= Number(parcel.length_cm)
					&& Number(rate.max_width_cm) >= Number(parcel.width_cm)
					&& Number(rate.max_height_cm) >= Number(parcel.height_cm))
				: null;
			const delivery = matchedRate
				? Number(matchedRate.price)
				: freeOver !== null && subtotal >= freeOver ? 0 : Number(selected.price);
			deliveryCostElement.textContent = delivery === 0 ? 'Free' : `£${delivery.toFixed(2)}`;
			totalElement.textContent = `£${(subtotal + delivery).toFixed(2)}`;
		};
		const refreshDelivery = () => {
			updateDeliveryOptions();
			updateDeliveryTotal();
		};
		deliverySelect.addEventListener('change', updateDeliveryTotal);
		if (shippingCountry) shippingCountry.addEventListener('change', refreshDelivery);
		if (billingCountry) billingCountry.addEventListener('change', refreshDelivery);
		if (shipToDifferentAddress) shipToDifferentAddress.addEventListener('change', refreshDelivery);
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
				const trackingUrlField = managementForm.querySelector('[name="tracking_url"]');
				const royalMailUrl = (number) => number ? `https://www.royalmail.com/track-your-item#/tracking-results/${encodeURIComponent(number)}` : '';
				let previousTrackingUrl = royalMailUrl(trackingField ? trackingField.value.trim() : '');
				const syncTrackingUrl = () => {
					if (!trackingField || !trackingUrlField) return;
					const nextUrl = royalMailUrl(trackingField.value.trim());
					if (!trackingUrlField.value.trim() || trackingUrlField.value.trim() === previousTrackingUrl) {
						trackingUrlField.value = nextUrl;
					}
					previousTrackingUrl = nextUrl;
				};
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
				trackingField && trackingField.addEventListener('input', syncTrackingUrl);
				syncTrackingUrl();
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
	const linkedOrderButton = document.querySelector('[data-order-auto-open]');
	if (linkedOrderButton) linkedOrderButton.click();
	document.addEventListener('click', (event) => {
		const copyButton = event.target.closest('[data-copy-target]');
		if (!copyButton) return;
		const input = document.getElementById(copyButton.dataset.copyTarget);
		if (!input) return;
		let feedback = copyButton.closest('.order-copy-field, .order-pdf-link').querySelector('.copy-feedback');
		if (!feedback) {
			feedback = document.createElement('span');
			feedback.className = 'copy-feedback';
			feedback.setAttribute('role', 'status');
			copyButton.parentElement.append(feedback);
		}
		const copyFailed = () => {
			feedback.textContent = 'Copy failed. Select and copy the details manually.';
			if (!input.hidden) {
				input.focus();
				input.select();
			} else {
				input.hidden = false;
				input.focus();
				input.select();
			}
		};
		if (!navigator.clipboard || !navigator.clipboard.writeText) {
			copyFailed();
			return;
		}
		navigator.clipboard.writeText(input.value).then(() => {
			feedback.textContent = 'Copied';
		}, copyFailed);
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
